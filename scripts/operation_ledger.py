"""Operation ledger for Dreamina Design (Task 5).

Tracks ``OperationReceipt`` instances keyed by ``submit_id``, persisting
them atomically to disk so they survive process restarts. The ledger
also exposes:

* a cancel-support probe (argv-only CLI call);
* a query path that asks the CLI ``status <submit_id>`` before any new
  submission is attempted, preventing blind resubmission of ambiguous
  operations.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


class OperationLedgerError(Exception):
    """Base class for operation ledger errors."""


class OperationNotFoundError(OperationLedgerError):
    """The submit_id is not in the ledger."""


class AmbiguousSubmissionError(OperationLedgerError):
    """The submit_id already terminated; resubmission would be ambiguous."""


class CancelNotSupportedError(OperationLedgerError):
    """The dreamina CLI reports that cancellation is not supported."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OperationLedger:
    """Append-only ledger of operation receipts keyed by ``submit_id``."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._ops_dir = self._root / "operations"
        self._ops_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / "index.json"

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def record(
        self,
        *,
        session_id: str,
        submit_id: str,
        mode: str,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        existing = self._read(submit_id)
        if existing is not None and existing.get("state") in TERMINAL_STATES:
            raise AmbiguousSubmissionError(
                f"submit_id {submit_id} already terminated with state {existing['state']}; "
                f"query it via status before any resubmission"
            )
        receipt = {
            "submit_id": submit_id,
            "session_id": session_id,
            "mode": mode,
            "request_fingerprint": request_fingerprint,
            "state": "queued",
            "submitted_at": _now_iso(),
            "updated_at": _now_iso(),
            "required_action": "wait",
            "history": [{"state": "queued", "at": _now_iso()}],
        }
        self._atomic_write(self._path_for(submit_id), receipt)
        self._index_add(submit_id, session_id)
        return receipt

    def update_state(
        self,
        *,
        submit_id: str,
        state: str,
        required_action: str | None = None,
        last_error_code: str | None = None,
    ) -> dict[str, Any]:
        receipt = self._read(submit_id)
        if receipt is None:
            raise OperationNotFoundError(submit_id)
        receipt["state"] = state
        receipt["updated_at"] = _now_iso()
        if required_action is not None:
            receipt["required_action"] = required_action
        if last_error_code is not None:
            receipt["last_error_code"] = last_error_code
        receipt.setdefault("history", []).append({"state": state, "at": _now_iso()})
        self._atomic_write(self._path_for(submit_id), receipt)
        return receipt

    def get(self, *, submit_id: str) -> dict[str, Any]:
        receipt = self._read(submit_id)
        if receipt is None:
            raise OperationNotFoundError(submit_id)
        return receipt

    # ------------------------------------------------------------------
    # CLI-driven discovery and querying
    # ------------------------------------------------------------------
    def discover_cancel_support(self, *, adapter: Any) -> bool:
        """Argv-only probe asking the CLI whether it supports cancellation."""
        result = adapter.run(["capabilities", "cancel"])
        payload = result.payload if isinstance(result.payload, Mapping) else {}
        return bool(payload.get("cancel_supported", False))

    def query(self, *, submit_id: str, adapter: Any) -> dict[str, Any]:
        if self._read(submit_id) is None:
            raise OperationNotFoundError(submit_id)
        result = adapter.run(["status", submit_id])
        payload = result.payload if isinstance(result.payload, Mapping) else {}
        new_state = str(payload.get("state", "unknown"))
        new_action = payload.get("required_action")
        return self.update_state(
            submit_id=submit_id,
            state=new_state,
            required_action=str(new_action) if new_action is not None else None,
        )

    def cancel(self, *, submit_id: str, adapter: Any) -> dict[str, Any]:
        if self._read(submit_id) is None:
            raise OperationNotFoundError(submit_id)
        if not self.discover_cancel_support(adapter=adapter):
            raise CancelNotSupportedError(
                "dreamina CLI reports cancel_supported=false; cannot cancel"
            )
        result = adapter.run(["cancel", submit_id])
        payload = result.payload if isinstance(result.payload, Mapping) else {}
        new_state = str(payload.get("state", "cancelled"))
        return self.update_state(
            submit_id=submit_id,
            state=new_state,
            required_action="report_failure",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _path_for(self, submit_id: str) -> Path:
        if not submit_id or "/" in submit_id or ".." in submit_id:
            raise ValueError(f"unsafe submit_id: {submit_id!r}")
        return self._ops_dir / f"{submit_id}.json"

    def _read(self, submit_id: str) -> dict[str, Any] | None:
        path = self._path_for(submit_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _atomic_write(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _index_add(self, submit_id: str, session_id: str) -> None:
        index = self._load_json(self._index_path, default={"submits": {}})
        index["submits"][submit_id] = session_id
        self._atomic_write(self._index_path, index)

    @staticmethod
    def _load_json(path: Path, default: Any = None) -> Any:
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "AmbiguousSubmissionError",
    "CancelNotSupportedError",
    "OperationLedger",
    "OperationNotFoundError",
    "TERMINAL_STATES",
]
