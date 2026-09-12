"""Approval guard for Dreamina Design (Task 5).

Wraps ``approval_receipt`` persistence with session boundaries, replay
detection, expiry enforcement, and strict non-secret storage.

Storage guarantees:

* Credential-shaped keys (``token``, ``api_key``, ``password``, …) are
  silently stripped before persistence.
* Account-snapshot keys (``account_id``, ``membership_tier``,
  ``credits_balance``) cause the receipt to be rejected outright —
  they must never reach the ledger.
* Prompts marked private (or any field whose name contains a private
  field token) are redacted before write.
* All writes are atomic (write-to-temp + rename) so a crash never
  leaves a half-written receipt.
* Receipts survive process restart; the same ``ApprovalGuard`` instance
  can be reconstructed from the root directory.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.image_service import build_request_fingerprint


# Silently stripped before persistence. These are common credential
# shapes that callers may forward accidentally.
STRIPPED_KEYS = {
    "token",
    "secret",
    "api_key",
    "apikey",
    "access_key",
    "session_token",
    "cookie",
    "private_key",
    "password",
    "auth",
    "authorization",
}

# Rejected outright. These are account-snapshot fields that the plugin
# never accepts in an approval receipt.
REJECTED_KEYS = {
    "account_id",
    "membership_tier",
    "credits_balance",
    "account_snapshot",
}


class ApprovalGuardError(Exception):
    """Base class for approval guard errors."""


class SessionExistsError(ApprovalGuardError):
    """A session with this label already exists."""


class SessionNotFoundError(ApprovalGuardError):
    """The requested session does not exist."""


class RequestChangedError(ApprovalGuardError):
    """The replayed request does not match the originally approved one."""


class ApprovalReplayMismatchError(ApprovalGuardError):
    """The recorded receipt's fingerprint does not match the request."""


class ApprovalExpiredError(ApprovalGuardError):
    """The approval receipt's expires_at is in the past."""


class SecretFieldError(ApprovalGuardError):
    """A forbidden account-snapshot field was supplied."""


SESSION_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,63}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _scrub(value: Any, private_tokens: tuple[str, ...]) -> Any:
    """Recursively strip forbidden keys and redact private tokens."""
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, inner in value.items():
            lkey = str(key).lower()
            if lkey in {k.lower() for k in REJECTED_KEYS}:
                raise SecretFieldError(f"forbidden account field in receipt: {key}")
            if lkey in {k.lower() for k in STRIPPED_KEYS}:
                # Silently strip credential-shaped keys.
                continue
            if any(token and token in str(key).lower() for token in private_tokens):
                cleaned[key] = "[REDACTED]"
                continue
            cleaned[key] = _scrub(inner, private_tokens)
        return cleaned
    if isinstance(value, list):
        return [_scrub(item, private_tokens) for item in value]
    return value


class ApprovalGuard:
    """Session-scoped approval receipt registry with non-secret persistence."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._sessions_dir = self._root / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def create_session(self, *, label: str) -> str:
        if not SESSION_LABEL_PATTERN.fullmatch(label):
            raise ValueError(f"invalid session label: {label!r}")
        index_path = self._sessions_dir / "index.json"
        index = self._load_json(index_path, default={"labels": {}})
        if label in index["labels"]:
            raise SessionExistsError(f"session already exists: {label}")
        session_id = secrets.token_urlsafe(12)
        index["labels"][label] = session_id
        self._atomic_write(index_path, index)
        session_dir = self._sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "session_id": session_id,
            "label": label,
            "created_at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._atomic_write(session_dir / "session.json", meta)
        return session_id

    def list_sessions(self) -> list[str]:
        index = self._load_json(self._sessions_dir / "index.json", default={"labels": {}})
        return sorted(index["labels"].values())

    def select_session(self, session_id: str) -> dict[str, Any]:
        session_dir = self._sessions_dir / session_id
        if not (session_dir / "session.json").is_file():
            raise SessionNotFoundError(session_id)
        return self._load_json(session_dir / "session.json")

    def delete_session(self, session_id: str) -> None:
        session_dir = self._sessions_dir / session_id
        if not (session_dir / "session.json").is_file():
            raise SessionNotFoundError(session_id)
        for entry in sorted(session_dir.rglob("*"), reverse=True):
            if entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                entry.rmdir()
        # Remove from index
        index = self._load_json(self._sessions_dir / "index.json", default={"labels": {}})
        for label, sid in list(index["labels"].items()):
            if sid == session_id:
                del index["labels"][label]
                break
        self._atomic_write(self._sessions_dir / "index.json", index)

    # ------------------------------------------------------------------
    # Approval recording and replay
    # ------------------------------------------------------------------
    def record_approval(
        self,
        session_id: str,
        *,
        request: Mapping[str, Any],
        receipt: Mapping[str, Any],
        private_fields: Iterable[str] = (),
    ) -> None:
        session_dir = self._sessions_dir / session_id
        if not (session_dir / "session.json").is_file():
            raise SessionNotFoundError(session_id)
        fingerprint = build_request_fingerprint(dict(request))
        private_tokens = tuple(str(token).lower() for token in private_fields)
        scrubbed = _scrub(dict(receipt), private_tokens)
        # Preserve the caller-supplied request_fingerprint rather than
        # silently rewriting it; that lets assert_approval_for detect a
        # mismatched receipt on replay.
        scrubbed.setdefault("request_fingerprint", fingerprint)
        scrubbed.setdefault("approved_at", _now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        # Also redact any explicit private field values from the original request
        # that callers flagged. We never persist the raw value, only the redaction
        # marker.
        if any(field in request for field in private_fields):
            scrubbed["redacted_fields"] = sorted(private_fields)
        receipt_path = session_dir / "approvals" / f"{fingerprint}.json"
        self._atomic_write(receipt_path, scrubbed)

    def assert_approval_for(self, session_id: str, *, request: Mapping[str, Any]) -> dict[str, Any]:
        session_dir = self._sessions_dir / session_id
        if not (session_dir / "session.json").is_file():
            raise SessionNotFoundError(session_id)
        fingerprint = build_request_fingerprint(dict(request))
        receipt_path = session_dir / "approvals" / f"{fingerprint}.json"
        if not receipt_path.is_file():
            raise RequestChangedError(
                f"no approval recorded for request fingerprint {fingerprint}"
            )
        receipt = self._load_json(receipt_path)
        if receipt.get("request_fingerprint") != fingerprint:
            raise ApprovalReplayMismatchError(
                f"approval fingerprint mismatch: expected {fingerprint}"
            )
        expires_at = receipt.get("expires_at")
        if expires_at:
            try:
                expiry = _parse_iso(expires_at)
            except ValueError as exc:
                raise ApprovalReplayMismatchError(f"invalid expires_at: {expires_at}") from exc
            if expiry <= _now():
                raise ApprovalExpiredError(f"approval expired at {expires_at}")
        return receipt

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _load_json(path: Path, default: Any = None) -> Any:
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
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


__all__ = [
    "ApprovalExpiredError",
    "ApprovalGuard",
    "ApprovalReplayMismatchError",
    "RequestChangedError",
    "SecretFieldError",
    "SessionExistsError",
    "SessionNotFoundError",
]
