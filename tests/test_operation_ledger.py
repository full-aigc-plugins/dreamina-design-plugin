"""RED tests for the Dreamina operation ledger (Task 5).

The operation ledger tracks submit IDs and terminal state transitions
across process restarts. It also handles cancel-support discovery
(argv-only probes) and ensures that ambiguous submissions are queried
by submit ID instead of resubmitted blindly.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - exercised by RED phase
    from scripts.operation_ledger import (  # type: ignore  # noqa: E402
        AmbiguousSubmissionError,
        CancelNotSupportedError,
        OperationLedger,
        OperationNotFoundError,
    )
except ModuleNotFoundError:  # pragma: no cover
    OperationLedger = None  # type: ignore[assignment]
    AmbiguousSubmissionError = None  # type: ignore[assignment]
    CancelNotSupportedError = None  # type: ignore[assignment]
    OperationNotFoundError = None  # type: ignore[assignment]


TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


class ModuleExportTests(unittest.TestCase):
    def test_module_exports_ledger(self) -> None:
        self.assertIsNotNone(OperationLedger)


class SubmitIdPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = OperationLedger(root=Path(self.tmp.name) / "ops")

    def test_record_and_lookup_submit_id(self) -> None:
        self.ledger.record(
            session_id="sess-1",
            submit_id="sub-001",
            mode="text2image",
            request_fingerprint="a" * 64,
        )
        op = self.ledger.get(submit_id="sub-001")
        self.assertEqual(op["submit_id"], "sub-001")
        self.assertEqual(op["session_id"], "sess-1")
        self.assertEqual(op["state"], "queued")

    def test_persistence_across_restart(self) -> None:
        root = Path(self.tmp.name) / "ops"
        ledger_a = OperationLedger(root=root)
        ledger_a.record(session_id="s1", submit_id="sub-A", mode="text2video", request_fingerprint="b" * 64)
        ledger_b = OperationLedger(root=root)
        op = ledger_b.get(submit_id="sub-A")
        self.assertEqual(op["session_id"], "s1")

    def test_batch_intent_and_result_bind_allowance_reservation(self) -> None:
        fingerprint = "d" * 64
        intent = self.ledger.begin_submission(
            session_id="ba_" + "2" * 32,
            mode="text2video",
            request_fingerprint=fingerprint,
            allowance_id="ba_" + "2" * 32,
            reservation_id="br_" + "3" * 32,
        )
        self.assertEqual(intent["allowance_id"], "ba_" + "2" * 32)
        self.assertEqual(intent["reservation_id"], "br_" + "3" * 32)
        result = self.ledger.complete_submission_intent(
            request_fingerprint=fingerprint,
            submit_id="submit_1",
            allowance_id="ba_" + "2" * 32,
            reservation_id="br_" + "3" * 32,
        )
        self.assertEqual(result["reservation_id"], "br_" + "3" * 32)

    def test_submission_intent_survives_crash_window_and_blocks_retry(self) -> None:
        fingerprint = "9" * 64
        self.ledger.begin_submission(session_id="s", mode="text2image", request_fingerprint=fingerprint)
        restarted = OperationLedger(root=Path(self.tmp.name) / "ops")
        with self.assertRaises(AmbiguousSubmissionError):
            restarted.begin_submission(session_id="s", mode="text2image", request_fingerprint=fingerprint)
        intent = restarted.complete_submission_intent(request_fingerprint=fingerprint, submit_id=None, error_code="TRANSPORT_UNKNOWN")
        self.assertEqual(intent["state"], "manual_review")

    def test_batch_execution_state_survives_restart(self) -> None:
        project_id = "vp_" + "1" * 24
        self.ledger.save_batch(project_id=project_id, batch_version="v001", payload={"state": "generating"})
        restarted = OperationLedger(root=Path(self.tmp.name) / "ops")
        self.assertEqual(restarted.load_batch(project_id=project_id, batch_version="v001"), {"state": "generating"})


class TerminalAndUnknownStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = OperationLedger(root=Path(self.tmp.name) / "ops")
        self.ledger.record(session_id="s1", submit_id="sub-Z", mode="text2image", request_fingerprint="c" * 64)

    def test_transition_to_succeeded_is_terminal(self) -> None:
        self.ledger.update_state(submit_id="sub-Z", state="succeeded", required_action="download")
        op = self.ledger.get(submit_id="sub-Z")
        self.assertEqual(op["state"], "succeeded")
        self.assertEqual(op["required_action"], "download")

    def test_transition_to_failed_is_terminal(self) -> None:
        self.ledger.update_state(submit_id="sub-Z", state="failed", required_action="report_failure")
        op = self.ledger.get(submit_id="sub-Z")
        self.assertEqual(op["state"], "failed")

    def test_unknown_state_is_recorded_with_query_required(self) -> None:
        self.ledger.update_state(submit_id="sub-Z", state="unknown", required_action="retry_query")
        op = self.ledger.get(submit_id="sub-Z")
        self.assertEqual(op["state"], "unknown")
        self.assertEqual(op["required_action"], "retry_query")

    def test_terminal_state_cannot_be_re_submitted(self) -> None:
        self.ledger.update_state(submit_id="sub-Z", state="succeeded")
        with self.assertRaises(AmbiguousSubmissionError):
            self.ledger.record(
                session_id="s1",
                submit_id="sub-Z",
                mode="text2image",
                request_fingerprint="c" * 64,
            )


class CancelSupportDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_cancel_is_not_advertised_by_current_cli_contract(self) -> None:
        ledger = OperationLedger(root=Path(self.tmp.name) / "ops")
        self.assertFalse(ledger.discover_cancel_support(adapter=None))

    def test_cancel_unsupported_raises_on_attempted_cancel(self) -> None:
        ledger = OperationLedger(root=Path(self.tmp.name) / "ops")
        ledger.record(session_id="s", submit_id="sub-c", mode="text2image", request_fingerprint="d" * 64)

        class _Adapter:
            def run(self, args):
                raise AssertionError(f"cancel must not call unsupported CLI command: {args}")

        with self.assertRaises(CancelNotSupportedError):
            ledger.cancel(submit_id="sub-c", adapter=_Adapter())


class QueryBySubmitIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = OperationLedger(root=Path(self.tmp.name) / "ops")

    def test_query_returns_state_without_resubmission(self) -> None:
        self.ledger.record(session_id="s", submit_id="sub-Q", mode="text2image", request_fingerprint="e" * 64)

        adapter_calls: list[list[str]] = []

        class _Adapter:
            def run(self, args):
                adapter_calls.append(list(args))
                from scripts.dreamina_adapter import DreaminaResult
                return DreaminaResult(
                    exit_code=0,
                    payload={"gen_status": "querying"},
                    error_code=None,
                    submit_id="sub-Q",
                    stderr="",
                )

        self.ledger.query(submit_id="sub-Q", adapter=_Adapter())
        self.assertEqual(adapter_calls[0], ["query_result", "--submit_id", "sub-Q"])
        op = self.ledger.get(submit_id="sub-Q")
        self.assertEqual(op["state"], "queued")

    def test_query_maps_success_to_download_and_fail_to_report(self) -> None:
        self.ledger.record(session_id="s", submit_id="sub-S", mode="text2image", request_fingerprint="f" * 64)

        class _Adapter:
            def __init__(self, status): self.status = status
            def run(self, args):
                from scripts.dreamina_adapter import DreaminaResult
                return DreaminaResult(exit_code=0, payload={"gen_status": self.status}, error_code=None, submit_id="sub-S", stderr="")

        succeeded = self.ledger.query(submit_id="sub-S", adapter=_Adapter("success"))
        self.assertEqual((succeeded["state"], succeeded["required_action"]), ("succeeded", "download"))

    def test_query_unknown_submit_id_raises(self) -> None:
        with self.assertRaises(OperationNotFoundError):
            self.ledger.query(submit_id="ghost", adapter=None)  # type: ignore[arg-type]

    def test_bounded_poll_queries_only_and_survives_restart(self) -> None:
        root = Path(self.tmp.name) / "ops"
        self.ledger.record(session_id="s", submit_id="sub-P", mode="text2image", request_fingerprint="1" * 64)
        calls = []

        class _Adapter:
            def run(self, args):
                calls.append(list(args))
                from scripts.dreamina_adapter import DreaminaResult
                status = "success" if len(calls) == 2 else "querying"
                return DreaminaResult(exit_code=0, payload={"gen_status": status}, error_code=None, submit_id="sub-P", stderr="")

        restarted = OperationLedger(root=root)
        result = restarted.poll_until_terminal(
            submit_id="sub-P", adapter=_Adapter(), max_attempts=3,
            interval_seconds=0, sleep_fn=lambda _: None,
        )
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(calls, [
            ["query_result", "--submit_id", "sub-P"],
            ["query_result", "--submit_id", "sub-P"],
        ])

    def test_poll_limit_records_unknown_without_resubmission(self) -> None:
        self.ledger.record(session_id="s", submit_id="sub-U", mode="text2image", request_fingerprint="2" * 64)

        class _Adapter:
            def run(self, args):
                from scripts.dreamina_adapter import DreaminaResult
                return DreaminaResult(exit_code=0, payload={"gen_status": "querying"}, error_code=None, submit_id="sub-U", stderr="")

        result = self.ledger.poll_until_terminal(
            submit_id="sub-U", adapter=_Adapter(), max_attempts=2,
            interval_seconds=0, sleep_fn=lambda _: None,
        )
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["last_error_code"], "POLL_LIMIT_REACHED")


if __name__ == "__main__":
    unittest.main()
