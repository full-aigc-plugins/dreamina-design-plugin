"""Durable, provider-neutral visual target and quality loop contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import stat
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from scripts.json_contracts import canonical_fingerprint, validate_contract


class InvalidVisualTransition(RuntimeError):
    """The requested loop operation is invalid for the durable current state."""


class VisualEvidenceError(ValueError):
    """A target, generated artifact, or Judge result is unsafe or malformed."""


class GenerationPort(Protocol):
    """Provider-neutral paid generation boundary."""

    def generate(self, request: dict[str, Any]) -> dict[str, Any]: ...


class JudgePort(Protocol):
    """Host-neutral independent visual evaluation boundary.

    Codex, Claude Code, ZCode, Kimi, or another MCP host can implement this
    port.  The domain layer never imports a host-specific sub-agent API.
    """

    def evaluate(self, evidence: dict[str, Any]) -> dict[str, Any]: ...


class DccPreviewPort(Protocol):
    """Optional Blender/Maya preview capture boundary for a later closed loop."""

    def capture(self, request: dict[str, Any]) -> dict[str, Any]: ...


class ReplanPort(Protocol):
    """Produce a repair candidate without submitting or spending credits."""

    def replan(self, evidence: dict[str, Any]) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise VisualEvidenceError("value must be canonical JSON") from exc


class VisualLoopStore:
    """Persist visual-loop state with private directories and atomic replacement."""

    _TERMINAL = frozenset({"completed", "stopped"})

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def create_loop(self, *, media_kind: str, max_attempts: int = 2) -> dict[str, Any]:
        if media_kind not in {"image", "video", "dcc_preview"}:
            raise ValueError("unsupported visual media kind")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between one and three")
        loop_id = "vl_" + secrets.token_hex(12)
        payload = {
            "schema_version": "1.0",
            "loop_id": loop_id,
            "media_kind": media_kind,
            "state": "draft",
            "max_attempts": max_attempts,
            "target": None,
            "rounds": [],
            "retry_allowance": None,
            "proposed_retry": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._write(loop_id, payload, create=True)
        return copy.deepcopy(payload)

    def get(self, loop_id: str) -> dict[str, Any]:
        path = self._path(loop_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KeyError(loop_id) from exc
        if payload.get("loop_id") != loop_id:
            raise VisualEvidenceError("visual loop identity mismatch")
        return payload

    def lock_target(
        self,
        loop_id: str,
        *,
        source_path: Path,
        approved_roots: Sequence[Path],
        mime_type: str,
        width: int,
        height: int,
        source: Mapping[str, Any],
    ) -> dict[str, Any]:
        loop = self.get(loop_id)
        if loop["state"] != "draft":
            raise InvalidVisualTransition("target is already locked")
        path, roots, digest, size = self._inspect_file(source_path, approved_roots)
        receipt_core = {
            "schema_version": "1.0",
            "target_id": "vt_" + digest[:24],
            "media_kind": loop["media_kind"],
            "source_path": str(path),
            "sha256": digest,
            "size_bytes": size,
            "mime_type": str(mime_type),
            "width": int(width),
            "height": int(height),
            "approved_roots": [str(root) for root in roots],
            "approved_roots_digest": canonical_fingerprint({"approved_roots": [str(root) for root in roots]}),
            "source": _canonical_copy(source),
            "created_at": _now(),
        }
        receipt = {**receipt_core, "receipt_fingerprint": canonical_fingerprint(receipt_core)}
        validate_contract(receipt, "visual_target_receipt.schema.json")
        loop["target"] = receipt
        loop["state"] = "target_locked"
        loop["updated_at"] = _now()
        self._write(loop_id, loop)
        return copy.deepcopy(loop)

    def activate_retry_allowance(
        self,
        loop_id: str,
        *,
        request_fingerprint: str,
        credit_ceiling: int,
        approver: str,
    ) -> dict[str, Any]:
        loop = self.get(loop_id)
        if loop["state"] not in {"awaiting_approval", "replan_required"}:
            raise InvalidVisualTransition("retry approval is not available")
        if len(loop["rounds"]) >= loop["max_attempts"] or len(request_fingerprint) != 64:
            raise InvalidVisualTransition("loop has no approved retry capacity")
        proposed = loop.get("proposed_retry")
        if isinstance(proposed, Mapping) and proposed.get("request_fingerprint") != request_fingerprint:
            raise InvalidVisualTransition("approval does not match the proposed retry")
        if isinstance(credit_ceiling, bool) or not isinstance(credit_ceiling, int) or credit_ceiling < 1:
            raise ValueError("credit_ceiling must be a positive integer")
        loop["retry_allowance"] = {
            "allowance_id": "va_" + secrets.token_hex(16),
            "request_fingerprint": request_fingerprint,
            "credit_ceiling": credit_ceiling,
            "approver": str(approver),
            "state": "active",
            "activated_at": _now(),
        }
        loop["state"] = "retry_ready"
        loop["updated_at"] = _now()
        self._write(loop_id, loop)
        return copy.deepcopy(loop)

    def record_retry_proposal(self, loop_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        loop = self.get(loop_id)
        if loop["state"] not in {"awaiting_approval", "replan_required"}:
            raise InvalidVisualTransition("visual loop is not waiting for a repair plan")
        if len(loop["rounds"]) >= loop["max_attempts"]:
            raise InvalidVisualTransition("maximum visual attempts reached")
        request_copy = _canonical_copy(request)
        ceiling = request_copy.get("credit_ceiling")
        if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 1:
            raise VisualEvidenceError("replanned request needs a positive credit_ceiling")
        loop["proposed_retry"] = {
            "request": request_copy,
            "request_fingerprint": canonical_fingerprint(request_copy),
            "credit_ceiling": ceiling,
            "created_at": _now(),
        }
        loop["updated_at"] = _now()
        self._write(loop_id, loop)
        return copy.deepcopy(loop)

    def reserve_round(
        self,
        loop_id: str,
        *,
        request_fingerprint: str,
        request_credit_ceiling: int,
        retry: bool,
    ) -> dict[str, Any]:
        loop = self.get(loop_id)
        if retry:
            allowance = loop.get("retry_allowance")
            if loop["state"] != "retry_ready" or not isinstance(allowance, Mapping):
                raise InvalidVisualTransition("retry is not approved")
            if allowance.get("state") != "active" or allowance.get("request_fingerprint") != request_fingerprint:
                raise InvalidVisualTransition("retry request is outside the approved allowance")
            if (
                isinstance(request_credit_ceiling, bool)
                or not isinstance(request_credit_ceiling, int)
                or request_credit_ceiling < 1
                or request_credit_ceiling > allowance["credit_ceiling"]
            ):
                raise InvalidVisualTransition("retry credits exceed the approved allowance")
            if len(loop["rounds"]) >= loop["max_attempts"]:
                raise InvalidVisualTransition("maximum visual attempts reached")
            allowance["state"] = "consumed"
            allowance["consumed_at"] = _now()
        elif loop["state"] != "target_locked" or loop["rounds"]:
            raise InvalidVisualTransition("first round is not available")
        loop["state"] = "submitting"
        loop["pending_request_fingerprint"] = request_fingerprint
        loop["pending_credit_ceiling"] = request_credit_ceiling
        loop["updated_at"] = _now()
        self._write(loop_id, loop)
        return copy.deepcopy(loop)

    def record_round(
        self,
        loop_id: str,
        *,
        request_fingerprint: str,
        artifact: Mapping[str, Any],
        judge_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        loop = self.get(loop_id)
        if loop["state"] != "submitting" or loop.get("pending_request_fingerprint") != request_fingerprint:
            raise InvalidVisualTransition("round was not reserved")
        target = loop["target"]
        artifact_copy = self._artifact(artifact, target["approved_roots"])
        judge = self._judge(judge_result)
        score = float(judge_result["score"])
        gaps = list(judge_result.get("blocking_gaps", []))
        round_number = len(loop["rounds"]) + 1
        if score >= 8.0 and not gaps:
            next_action, state = "complete", "completed"
        elif round_number == 1 and loop["max_attempts"] >= 2:
            next_action, state = "request_retry_approval", "awaiting_approval"
        else:
            prior = loop["rounds"][-1] if loop["rounds"] else None
            repeated = bool(prior and set(prior["blocking_gaps"]) & set(gaps))
            improvement = score - float(prior["score"]) if prior else score
            next_action = "replan" if repeated or improvement < 1.0 else "manual_review"
            state = "replan_required" if next_action == "replan" else "manual_review"
        core = {
            "schema_version": "1.0",
            "round_id": "vr_" + secrets.token_hex(12),
            "loop_id": loop_id,
            "round": round_number,
            "target_id": target["target_id"],
            "target_sha256": target["sha256"],
            "request_fingerprint": request_fingerprint,
            "submit_id": str(artifact["submit_id"]),
            "artifact": artifact_copy,
            "judge": judge,
            "score": score,
            "blocking_gaps": gaps,
            "next_action": next_action,
            "created_at": _now(),
        }
        receipt = {**core, "receipt_fingerprint": canonical_fingerprint(core)}
        validate_contract(receipt, "visual_round_receipt.schema.json")
        loop["rounds"].append(receipt)
        loop["state"] = state
        loop.pop("pending_request_fingerprint", None)
        loop.pop("pending_credit_ceiling", None)
        loop["proposed_retry"] = None
        loop["updated_at"] = _now()
        self._write(loop_id, loop)
        return copy.deepcopy(loop)

    def mark_submission_unknown(self, loop_id: str, *, message: str) -> dict[str, Any]:
        loop = self.get(loop_id)
        if loop["state"] != "submitting":
            raise InvalidVisualTransition("no active submission")
        loop["state"] = "manual_review"
        loop["last_error"] = str(message)[:500]
        loop["updated_at"] = _now()
        self._write(loop_id, loop)
        return copy.deepcopy(loop)

    def stop(self, loop_id: str, *, reason: str) -> dict[str, Any]:
        loop = self.get(loop_id)
        if loop["state"] in self._TERMINAL:
            raise InvalidVisualTransition("visual loop is already terminal")
        loop["state"] = "stopped"
        loop["stop_reason"] = str(reason)[:500]
        loop["remote_cancelled"] = False
        loop["updated_at"] = _now()
        self._write(loop_id, loop)
        return copy.deepcopy(loop)

    @staticmethod
    def _judge(payload: Mapping[str, Any]) -> dict[str, Any]:
        value = _canonical_copy(payload)
        required = {"provider", "model", "evaluated_at", "rubric_version", "score", "gates", "blocking_gaps"}
        if set(value) != required:
            raise VisualEvidenceError("Judge result fields must be complete and closed")
        score = value["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 10:
            raise VisualEvidenceError("Judge score must be between zero and ten")
        if not isinstance(value["gates"], Mapping) or not isinstance(value["blocking_gaps"], list):
            raise VisualEvidenceError("Judge gates and blocking gaps are invalid")
        return {key: value[key] for key in ("provider", "model", "evaluated_at", "rubric_version", "gates")}

    @classmethod
    def _artifact(cls, artifact: Mapping[str, Any], approved_roots: Sequence[str]) -> dict[str, Any]:
        required = {"submit_id", "path", "sha256", "size_bytes", "mime_type"}
        if set(artifact) != required:
            raise VisualEvidenceError("artifact fields must be complete and closed")
        path, _, digest, size = cls._inspect_file(Path(str(artifact["path"])), [Path(root) for root in approved_roots])
        if digest != artifact["sha256"] or size != artifact["size_bytes"]:
            raise VisualEvidenceError("artifact changed after generation")
        return {"path": str(path), "sha256": digest, "size_bytes": size, "mime_type": str(artifact["mime_type"])}

    @staticmethod
    def _inspect_file(source_path: Path, approved_roots: Sequence[Path]) -> tuple[Path, tuple[Path, ...], str, int]:
        source = Path(source_path)
        if not source.is_absolute() or source.is_symlink():
            raise VisualEvidenceError("visual evidence must be an absolute regular non-symlink file")
        roots = tuple(sorted({Path(root).resolve(strict=True) for root in approved_roots}, key=str))
        resolved = source.resolve(strict=True)
        if not roots or not any(resolved == root or root in resolved.parents for root in roots):
            raise VisualEvidenceError("visual evidence is outside approved roots")
        before = resolved.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size < 1:
            raise VisualEvidenceError("visual evidence must be a non-empty regular file")
        digest = hashlib.sha256()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(resolved, flags), "rb") as reader:
            opened = os.fstat(reader.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise VisualEvidenceError("visual evidence changed during open")
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(reader.fileno())
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
        ):
            raise VisualEvidenceError("visual evidence changed while hashing")
        return resolved, roots, digest.hexdigest(), before.st_size

    def _path(self, loop_id: str) -> Path:
        if not isinstance(loop_id, str) or not loop_id.startswith("vl_") or len(loop_id) != 27:
            raise KeyError(loop_id)
        return self.root / f"{loop_id}.json"

    def _write(self, loop_id: str, payload: Mapping[str, Any], *, create: bool = False) -> None:
        validate_contract(payload, "visual_loop_state.schema.json")
        target = self._path(loop_id)
        if create and target.exists():
            raise InvalidVisualTransition("visual loop already exists")
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{loop_id}.", suffix=".tmp", dir=self.root)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as writer:
                writer.write(encoded)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temporary, target)
            directory = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


class VisualLoopService:
    """Run one paid round at a time, then evaluate without implicit resubmission."""

    def __init__(self, store: VisualLoopStore, *, generation_port: GenerationPort, judge_port: JudgePort) -> None:
        self.store = store
        self.generation_port = generation_port
        self.judge_port = judge_port

    @staticmethod
    def request_fingerprint(request: Mapping[str, Any]) -> str:
        return canonical_fingerprint(_canonical_copy(request))

    def run_first_round(self, loop_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._run(loop_id, request, retry=False)

    def run_retry(self, loop_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._run(loop_id, request, retry=True)

    def propose_retry(self, loop_id: str, replan_port: ReplanPort) -> dict[str, Any]:
        loop = self.store.get(loop_id)
        evidence = {
            "target": copy.deepcopy(loop.get("target")),
            "rounds": copy.deepcopy(loop.get("rounds", [])),
            "remaining_attempts": loop["max_attempts"] - len(loop["rounds"]),
        }
        request = replan_port.replan(evidence)
        if not isinstance(request, Mapping):
            raise VisualEvidenceError("ReplanPort must return one request object")
        return self.store.record_retry_proposal(loop_id, request)

    def run_dcc_preview(
        self,
        loop_id: str,
        request: Mapping[str, Any],
        *,
        preview_port: DccPreviewPort,
    ) -> dict[str, Any]:
        loop = self.store.get(loop_id)
        if loop["media_kind"] != "dcc_preview":
            raise InvalidVisualTransition("DCC preview requires a dcc_preview loop")
        request_copy = _canonical_copy(request)
        fingerprint = self.request_fingerprint(request_copy)
        reserved = self.store.reserve_round(
            loop_id,
            request_fingerprint=fingerprint,
            request_credit_ceiling=0,
            retry=False,
        )
        try:
            artifact = preview_port.capture(request_copy)
            judged = self.judge_port.evaluate({
                "target": copy.deepcopy(reserved["target"]),
                "artifact": copy.deepcopy(artifact),
                "previous_round": None,
            })
            return self.store.record_round(
                loop_id,
                request_fingerprint=fingerprint,
                artifact=artifact,
                judge_result=judged,
            )
        except Exception as exc:
            try:
                self.store.mark_submission_unknown(loop_id, message=str(exc))
            except InvalidVisualTransition:
                pass
            raise

    def _run(self, loop_id: str, request: Mapping[str, Any], *, retry: bool) -> dict[str, Any]:
        request_copy = _canonical_copy(request)
        fingerprint = self.request_fingerprint(request_copy)
        raw_ceiling = request_copy.get("credit_ceiling", 1)
        if isinstance(raw_ceiling, bool) or not isinstance(raw_ceiling, int) or raw_ceiling < 1:
            raise VisualEvidenceError("generation request credit_ceiling must be a positive integer")
        loop = self.store.reserve_round(
            loop_id,
            request_fingerprint=fingerprint,
            request_credit_ceiling=raw_ceiling,
            retry=retry,
        )
        try:
            artifact = self.generation_port.generate(request_copy)
            evidence = {
                "target": copy.deepcopy(loop["target"]),
                "artifact": copy.deepcopy(artifact),
                "previous_round": copy.deepcopy(loop["rounds"][-1]) if loop["rounds"] else None,
            }
            judged = self.judge_port.evaluate(evidence)
            return self.store.record_round(
                loop_id,
                request_fingerprint=fingerprint,
                artifact=artifact,
                judge_result=judged,
            )
        except Exception as exc:
            try:
                self.store.mark_submission_unknown(loop_id, message=str(exc))
            except InvalidVisualTransition:
                pass
            raise


__all__ = [
    "DccPreviewPort",
    "GenerationPort",
    "InvalidVisualTransition",
    "JudgePort",
    "ReplanPort",
    "VisualEvidenceError",
    "VisualLoopService",
    "VisualLoopStore",
]
