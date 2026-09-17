"""RED tests for the gate-decision recorder.

Option (b) of the two closing paths has a mechanism:
``unlock_runtime_gates.py probe`` / ``record-canary``. Option (a) —
accepting the plan's ``explicitly blocked`` / ``NOT_RUN`` disjunct as the
closing state — had none, so the owner would have to hand-edit prose.

This recorder gives (a) the same shape as (b): **the human supplies the
decision**, the tool only records it. It must therefore:

* refuse to run without an explicit, non-placeholder approver — the model
  must never be able to generate its own acceptance record;
* write a decision record naming the approver, the timestamp, the reason,
  and citing the plan's disjunction text it relies on;
* update the two audit rows in ``offline.md`` from ``BLOCKED`` /
  ``NOT_RUN`` to an acceptance wording that does **not** claim the gates
  were observed or approved;
* be idempotent and refuse to double-record.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - RED phase
    from scripts.record_gate_decision import (  # type: ignore  # noqa: E402
        AlreadyRecordedError,
        ApprovalRequiredError,
        GateDecisionRecorder,
    )
except ModuleNotFoundError:  # pragma: no cover
    GateDecisionRecorder = None  # type: ignore[assignment]
    ApprovalRequiredError = None  # type: ignore[assignment]
    AlreadyRecordedError = None  # type: ignore[assignment]


AUDIT_ROWS = (
    "| `read_only_runtime_contract observed or blocked` | \u2705 BLOCKED      | "
    "\u00a72 + `docs/verification/dreamina-cli-runtime.md` + "
    "`docs/verification/authorization-decision.md` |\n"
    "| `paid_canary = separately approved or NOT_RUN`  | \u2705 NOT_RUN      | "
    "\u00a72 canary gate + `docs/verification/authorization-decision.md` |\n"
)


def _fake_repo(root: Path) -> Path:
    verification = root / "docs" / "verification"
    verification.mkdir(parents=True, exist_ok=True)
    (verification / "offline.md").write_text(
        "# Offline verification evidence\n\n"
        "## 8. Plan completion gate audit\n\n"
        "| Gate | Status | Evidence |\n"
        "|------|--------|----------|\n"
        "| `secret_matches = 0` | \u2705 PASS | \u00a72 + \u00a75 |\n"
        + AUDIT_ROWS,
        encoding="utf-8",
    )
    return root


class ModuleExportTests(unittest.TestCase):
    def test_module_exports_recorder(self) -> None:
        self.assertIsNotNone(GateDecisionRecorder)


class ApproverRequiredTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = _fake_repo(Path(self.tmp.name))

    def test_missing_approver_is_refused(self) -> None:
        recorder = GateDecisionRecorder(root=self.root)
        with self.assertRaises(ApprovalRequiredError):
            recorder.accept_blocked(approver="", reason="no CLI available")

    def test_placeholder_approver_is_refused(self) -> None:
        recorder = GateDecisionRecorder(root=self.root)
        for placeholder in ("TBD", "model", "assistant", "test", "unknown", "n/a"):
            with self.assertRaises(ApprovalRequiredError):
                recorder.accept_blocked(
                    approver=placeholder, reason="no CLI available"
                )

    def test_nothing_written_when_refused(self) -> None:
        recorder = GateDecisionRecorder(root=self.root)
        try:
            recorder.accept_blocked(approver="", reason="x")
        except ApprovalRequiredError:
            pass
        marker = self.root / "docs" / "verification" / "gate-decision-accepted.md"
        self.assertFalse(marker.exists())


class AcceptanceRecordingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = _fake_repo(Path(self.tmp.name))

    def test_records_decision_and_updates_audit_rows(self) -> None:
        recorder = GateDecisionRecorder(root=self.root)
        marker = recorder.accept_blocked(
            approver="wandl",
            reason="dreamina CLI is not available on this machine",
        )
        text = marker.read_text(encoding="utf-8")
        self.assertIn("wandl", text)
        self.assertIn("explicitly blocked", text)
        self.assertIn("NOT_RUN", text)
        self.assertRegex(text, r"\d{4}-\d{2}-\d{2}")

        offline = (self.root / "docs" / "verification" / "offline.md").read_text(
            encoding="utf-8"
        )
        # The rows must no longer be the unresolved wording.
        self.assertNotIn("\u2705 BLOCKED", offline)
        self.assertNotIn("\u2705 NOT_RUN", offline)
        # The status column records the owner's acceptance, and the evidence
        # column names the disjunct relied on.
        self.assertIn("\u2705 owner-accepted", offline)
        self.assertIn("explicitly blocked** disjunct", offline)
        self.assertIn("NOT_RUN** disjunct", offline)
        # It must not read as though the gates were observed/approved.
        self.assertNotIn("\u2705 observed", offline)
        self.assertNotIn("\u2705 APPROVED", offline)

    def test_does_not_claim_observed_or_approved(self) -> None:
        recorder = GateDecisionRecorder(root=self.root)
        marker = recorder.accept_blocked(approver="wandl", reason="deferred")
        text = marker.read_text(encoding="utf-8")
        # The marker may *quote* the plan's gate line as context; what it must
        # not do is assert the observed / APPROVED state.
        self.assertNotIn("is **`observed`**", text)
        self.assertNotIn("is **`APPROVED`**", text)
        self.assertIn("is **`explicitly blocked`**", text)
        self.assertIn("is **`NOT_RUN`**", text)

    def test_second_recording_is_refused(self) -> None:
        recorder = GateDecisionRecorder(root=self.root)
        recorder.accept_blocked(approver="wandl", reason="first")
        with self.assertRaises(AlreadyRecordedError):
            recorder.accept_blocked(approver="wandl", reason="second")

    def test_other_audit_rows_untouched(self) -> None:
        recorder = GateDecisionRecorder(root=self.root)
        recorder.accept_blocked(approver="wandl", reason="deferred")
        offline = (self.root / "docs" / "verification" / "offline.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("| `secret_matches = 0` | \u2705 PASS |", offline)


if __name__ == "__main__":
    unittest.main()
