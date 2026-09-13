"""Durable, allowance-scoped execution of an approved Dreamina video batch."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.operation_ledger import OperationLedger
from scripts.task_service import TaskService
from scripts.video_service import VideoService


class VideoBatchExecutor:
    """Submit deterministically and resume only through known provider task ids."""

    def __init__(self, *, project_store: Any, allowance: Any, allowance_id: str,
                 video_service: VideoService | None, adapter: Any,
                 download_root: Path | None = None) -> None:
        self._store = project_store
        self._allowance = allowance
        self._allowance_id = allowance_id
        self._adapter = adapter
        self._root = project_store.project_root(self._project_id_from_allowance()) / "video_batch_execution"
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._ledger = OperationLedger(self._root)
        self._service = video_service or VideoService({"modes": []}, self._root / "operations")
        self._tasks = TaskService(adapter)
        self._downloads = Path(download_root) if download_root else self._root / "downloads"

    def _project_id_from_allowance(self) -> str:
        return str(self._allowance.get(self._allowance_id)["project_id"])

    def _quote(self, project_id: str, batch_version: str) -> dict[str, Any]:
        allowance = self._allowance.get(self._allowance_id)
        if allowance.get("project_id") != project_id or allowance.get("quote_version") != batch_version:
            raise ValueError("batch identity is outside the activated allowance")
        return self._store.read_version(project_id, "video_batch_quote", batch_version, "video_batch_quote.schema.json")

    @staticmethod
    def _ordered_attempts(quote: dict[str, Any]) -> list[dict[str, Any]]:
        ordered = []
        for shot_index, item in enumerate(quote["items"]):
            for attempt in item["attempts"]:
                ordered.append({"shot_index": shot_index, "shot_id": item["shot_id"], **attempt})
        return sorted(ordered, key=lambda item: (item["shot_index"], item["attempt_number"]))

    def _load(self, project_id: str, batch_version: str) -> dict[str, Any]:
        state = self._ledger.load_batch(project_id=project_id, batch_version=batch_version)
        return state or {"project_id": project_id, "batch_version": batch_version,
                         "allowance_id": self._allowance_id, "state": "ready", "tasks": []}

    def _save(self, state: dict[str, Any]) -> None:
        self._ledger.save_batch(project_id=state["project_id"], batch_version=state["batch_version"], payload=state)

    def run_next(self, project_id: str, batch_version: str, max_new_submissions: int) -> dict[str, Any]:
        if isinstance(max_new_submissions, bool) or not isinstance(max_new_submissions, int) or not 1 <= max_new_submissions <= 4:
            raise ValueError("max_new_submissions must be between 1 and 4")
        quote, state = self._quote(project_id, batch_version), self._load(project_id, batch_version)
        if state["state"] == "manual_review": return {**state, "new_submissions": 0}
        known = {(item["shot_id"], item["attempt"]) for item in state["tasks"]}
        new_count = 0
        for planned in self._ordered_attempts(quote):
            key = (planned["shot_id"], planned["attempt_number"])
            if key in known or new_count >= max_new_submissions: continue
            if planned["attempt_number"] > 1:
                previous = next((item for item in state["tasks"] if item["shot_id"] == planned["shot_id"]
                                 and item["attempt"] == planned["attempt_number"] - 1), None)
                if previous is None or previous["state"] != "evaluation_retryable":
                    continue
            task = {"shot_index": planned["shot_index"], "shot_id": planned["shot_id"],
                    "attempt": planned["attempt_number"], "request_fingerprint": planned["request_fingerprint"],
                    "state": "submitting", "artifacts": []}
            state["tasks"].append(task); state["state"] = "submitting"; self._save(state)
            try:
                result = self._service.submit_with_batch_allowance(
                    planned["request"], adapter=self._adapter, allowance=self._allowance,
                    allowance_id=self._allowance_id, shot_id=planned["shot_id"],
                    attempt=planned["attempt_number"])
            except Exception as exc:
                task.update(state="manual_review", error_code=type(exc).__name__.upper())
                state["state"] = "manual_review"; self._save(state); break
            task.update(state="queued", submit_id=result["submit_id"],
                        reservation_id=result["reservation"]["reservation_id"])
            state["state"] = "generating"; new_count += 1; self._save(state)
        return {**state, "new_submissions": new_count}

    def reconcile(self, project_id: str, batch_version: str) -> dict[str, Any]:
        self._quote(project_id, batch_version)
        state = self._load(project_id, batch_version)
        for task in sorted(state["tasks"], key=lambda item: (item["shot_index"], item["attempt"])):
            if task["state"] == "submitting" and not task.get("submit_id"):
                task["state"] = "manual_review"
                task["error_code"] = "PROCESS_INTERRUPTED_AT_INVOCATION_BOUNDARY"
                state["state"] = "manual_review"; self._save(state); continue
            if not task.get("submit_id") or task["state"] in {"failed", "evaluation_retryable"}: continue
            task["state"] = "querying"; self._save(state)
            queried = self._tasks.query(task["submit_id"])
            status = str(queried["result"].get("gen_status", "unknown")).lower()
            if status in {"fail", "failed"}:
                task["state"] = "failed"; state["state"] = "failed"; self._save(state); continue
            if status != "success":
                task["state"] = "queued" if status == "querying" else "manual_review"
                state["state"] = "generating" if status == "querying" else "manual_review"; self._save(state); continue
            attempt_root = self._downloads / task["shot_id"] / f"attempt-{task['attempt']}"
            sequence = len(list(attempt_root.glob("download-*"))) + 1 if attempt_root.exists() else 1
            target = attempt_root / f"download-{sequence:03d}"
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            task["state"] = "downloading"; task["download_dir"] = str(target); self._save(state)
            downloaded = self._tasks.query(task["submit_id"], download_dir=str(target))
            task["artifacts"] = downloaded["artifacts"]
            task["state"] = "evaluation_retryable" if task["artifacts"] else "missing_artifact"
            state["state"] = task["state"]; self._save(state)
        return state

    def resume(self, project_id: str, batch_version: str) -> dict[str, Any]:
        """Reconcile known submit ids; this method never creates a submission."""
        return self.reconcile(project_id, batch_version)


__all__ = ["VideoBatchExecutor"]
