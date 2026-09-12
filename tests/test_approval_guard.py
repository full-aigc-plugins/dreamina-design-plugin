"""RED tests for the Dreamina approval guard (Task 5).

The approval guard wraps the ``approval_receipt`` schema and adds:

* session create / list / select / delete boundaries;
* request change detection (replay with mutated request is refused);
* approval expiry (TTL-driven invalidation);
* non-secret persistence (no tokens, no full private prompts, no
  account snapshots).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - exercised by RED phase
    from scripts.approval_guard import (  # type: ignore  # noqa: E402
        ApprovalExpiredError,
        ApprovalGuard,
        ApprovalReplayMismatchError,
        RequestChangedError,
        SecretFieldError,
        SessionExistsError,
        SessionNotFoundError,
    )
except ModuleNotFoundError:  # pragma: no cover
    ApprovalGuard = None  # type: ignore[assignment]
    ApprovalExpiredError = None  # type: ignore[assignment]
    ApprovalReplayMismatchError = None  # type: ignore[assignment]
    RequestChangedError = None  # type: ignore[assignment]
    SecretFieldError = None  # type: ignore[assignment]
    SessionExistsError = None  # type: ignore[assignment]
    SessionNotFoundError = None  # type: ignore[assignment]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _approval(fingerprint: str, *, expires_in_seconds: int | None = None, **extra) -> dict:
    payload = {
        "request_fingerprint": fingerprint,
        "acknowledged_cost": "credits",
        "acknowledged_scope": {"count": 1, "model": "seedream-5.0-pro", "resolution": "1k"},
        "approved_at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "approver": "tester",
    }
    if expires_in_seconds is not None:
        payload["expires_at"] = (
            _now() + timedelta(seconds=expires_in_seconds)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload.update(extra)
    return payload


class ModuleExportTests(unittest.TestCase):
    def test_module_exports_guard(self) -> None:
        self.assertIsNotNone(ApprovalGuard)


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.guard = ApprovalGuard(root=Path(self.tmp.name) / "sessions")

    def test_create_session_returns_id(self) -> None:
        session_id = self.guard.create_session(label="my session")
        self.assertTrue(session_id)
        self.assertIn(session_id, self.guard.list_sessions())

    def test_duplicate_session_label_raises(self) -> None:
        self.guard.create_session(label="dup")
        with self.assertRaises(SessionExistsError):
            self.guard.create_session(label="dup")

    def test_select_unknown_session_raises(self) -> None:
        with self.assertRaises(SessionNotFoundError):
            self.guard.select_session("does-not-exist")

    def test_delete_session_removes_it(self) -> None:
        session_id = self.guard.create_session(label="ephemeral")
        self.guard.delete_session(session_id)
        self.assertNotIn(session_id, self.guard.list_sessions())

    def test_delete_unknown_session_raises(self) -> None:
        with self.assertRaises(SessionNotFoundError):
            self.guard.delete_session("missing")


class ApprovalReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.guard = ApprovalGuard(root=Path(self.tmp.name) / "sessions")
        self.session_id = self.guard.create_session(label="replay")

    def test_record_and_replay_same_request_succeeds(self) -> None:
        request = {"mode": "text2image", "prompt": "x", "model": "seedream-5.0-pro", "count": 1, "resolution_type": "1k"}
        from scripts.image_service import build_request_fingerprint  # local import OK
        fingerprint = build_request_fingerprint(request)
        receipt = _approval(fingerprint)
        self.guard.record_approval(self.session_id, request=request, receipt=receipt)
        self.guard.assert_approval_for(self.session_id, request=request)

    def test_replay_with_changed_request_raises(self) -> None:
        request = {"mode": "text2image", "prompt": "x", "model": "seedream-5.0-pro", "count": 1, "resolution_type": "1k"}
        from scripts.image_service import build_request_fingerprint
        fingerprint = build_request_fingerprint(request)
        receipt = _approval(fingerprint)
        self.guard.record_approval(self.session_id, request=request, receipt=receipt)
        mutated = dict(request, prompt="changed")
        with self.assertRaises(RequestChangedError):
            self.guard.assert_approval_for(self.session_id, request=mutated)

    def test_replay_with_wrong_fingerprint_raises(self) -> None:
        request = {"mode": "text2image", "prompt": "x", "model": "seedream-5.0-pro", "count": 1, "resolution_type": "1k"}
        receipt = _approval("a" * 64)
        self.guard.record_approval(self.session_id, request=request, receipt=receipt)
        with self.assertRaises(ApprovalReplayMismatchError):
            self.guard.assert_approval_for(self.session_id, request=request)


class ApprovalExpiryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.guard = ApprovalGuard(root=Path(self.tmp.name) / "sessions")
        self.session_id = self.guard.create_session(label="expiry")

    def test_expired_approval_raises(self) -> None:
        request = {"mode": "text2image", "prompt": "x", "model": "seedream-5.0-pro", "count": 1, "resolution_type": "1k"}
        from scripts.image_service import build_request_fingerprint
        fingerprint = build_request_fingerprint(request)
        receipt = _approval(fingerprint, expires_in_seconds=-10)
        self.guard.record_approval(self.session_id, request=request, receipt=receipt)
        with self.assertRaises(ApprovalExpiredError):
            self.guard.assert_approval_for(self.session_id, request=request)


class NonSecretPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_persisted_file_omits_secrets(self) -> None:
        guard = ApprovalGuard(root=Path(self.tmp.name) / "sessions")
        session_id = guard.create_session(label="secrets")
        request = {"mode": "text2image", "prompt": "x", "model": "seedream-5.0-pro", "count": 1, "resolution_type": "1k"}
        from scripts.image_service import build_request_fingerprint
        fingerprint = build_request_fingerprint(request)
        receipt = _approval(fingerprint)
        receipt["token"] = "AKIA" + "A" * 32  # must never be persisted
        guard.record_approval(session_id, request=request, receipt=receipt)
        ledger_files = list(Path(self.tmp.name, "sessions").rglob("*.json"))
        self.assertTrue(ledger_files, "expected at least one persisted receipt file")
        for path in ledger_files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("AKIA", text)
            self.assertNotIn("token", text)

    def test_full_prompt_marked_private_is_redacted(self) -> None:
        guard = ApprovalGuard(root=Path(self.tmp.name) / "sessions")
        session_id = guard.create_session(label="private")
        request = {"mode": "text2image", "prompt": "secret phrase", "model": "seedream-5.0-pro", "count": 1, "resolution_type": "1k"}
        from scripts.image_service import build_request_fingerprint
        fingerprint = build_request_fingerprint(request)
        receipt = _approval(fingerprint)
        receipt["private_prompt"] = request["prompt"]
        guard.record_approval(session_id, request=request, receipt=receipt, private_fields=("prompt",))
        ledger_files = list(Path(self.tmp.name, "sessions").rglob("*.json"))
        joined = "\n".join(p.read_text(encoding="utf-8") for p in ledger_files)
        self.assertNotIn("secret phrase", joined)

    def test_account_snapshot_fields_rejected(self) -> None:
        guard = ApprovalGuard(root=Path(self.tmp.name) / "sessions")
        session_id = guard.create_session(label="account")
        request = {"mode": "text2image", "prompt": "x", "model": "seedream-5.0-pro", "count": 1, "resolution_type": "1k"}
        from scripts.image_service import build_request_fingerprint
        fingerprint = build_request_fingerprint(request)
        receipt = _approval(fingerprint)
        receipt["account_id"] = "acct-12345"
        receipt["membership_tier"] = "gold"
        with self.assertRaises(SecretFieldError):
            guard.record_approval(session_id, request=request, receipt=receipt)


class ProcessRestartTests(unittest.TestCase):
    def test_approvals_persist_across_guard_restart(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "sessions"

        guard_a = ApprovalGuard(root=root)
        session_id = guard_a.create_session(label="persistent")
        request = {"mode": "text2image", "prompt": "x", "model": "seedream-5.0-pro", "count": 1, "resolution_type": "1k"}
        from scripts.image_service import build_request_fingerprint
        fingerprint = build_request_fingerprint(request)
        receipt = _approval(fingerprint)
        guard_a.record_approval(session_id, request=request, receipt=receipt)

        # Simulate process restart by constructing a new guard instance.
        guard_b = ApprovalGuard(root=root)
        self.assertIn(session_id, guard_b.list_sessions())
        guard_b.assert_approval_for(session_id, request=request)


if __name__ == "__main__":
    unittest.main()
