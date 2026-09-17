"""Behavior tests for the durable automatic Dreamina 3D orchestrator."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "seedance_pipeline"))

import auto_orchestrator
from job_ledger import ExecutionPolicy, JobLedger, JobState, new_job


class FakeDesignClient:
    def __init__(self, responses):
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls = []

    def invoke(self, action, arguments):
        self.calls.append((action, dict(arguments)))
        values = self.responses.get(action, [])
        if not values:
            raise AssertionError(f"unexpected design action: {action}")
        return values.pop(0)


def _preview(path: Path) -> dict:
    payload = path.read_bytes()
    return {
        "schema_version": "1.0.0",
        "producer_plugin": "codex-blender",
        "producer_version": "0.2.0",
        "artifact_id": "blender_preview_001",
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "codec": "h264",
        "container": "mp4",
        "dimensions": {"width": 320, "height": 180},
        "fps": 24.0,
        "duration_seconds": 2.0,
        "bytes": len(payload),
        "camera": {"name": "Camera"},
        "frame_range": {"start": 1, "end": 48},
        "preview_mode": "camera_render",
        "restoration": {"status": "confirmed"},
    }


def _request() -> dict:
    return {
        "prompt": "cinematic orbit",
        "model": "seedance2.5",
        "resolution": "480p",
        "ratio": "16:9",
        "duration_seconds": 4,
    }


class AutoOrchestratorPresenceTests(unittest.TestCase):
    def test_automatic_orchestrator_is_implemented(self) -> None:
        self.assertTrue(
            (ROOT / "scripts" / "seedance_pipeline" / "auto_orchestrator.py").is_file(),
            "production automatic routing requires scripts/auto_orchestrator.py",
        )
        self.assertTrue(
            callable(getattr(auto_orchestrator, "run_until_blocked", None)),
            "auto_orchestrator must expose run_until_blocked",
        )


class AutomaticWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            callable(getattr(auto_orchestrator, "run_until_blocked", None)),
            "auto_orchestrator must expose run_until_blocked",
        )
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.preview_path = self.root / "preview.mp4"
        self.preview_path.write_bytes(b"preview-media")
        self.result_path = self.root / "result.mp4"
        self.result_path.write_bytes(b"final-media")
        self.result_artifact = {
            "path": str(self.result_path),
            "sha256": hashlib.sha256(self.result_path.read_bytes()).hexdigest(),
        }
        self.ledger_path = self.root / "job.json"

    def _ledger(self, *, max_charge="2.00"):
        ledger = JobLedger(self.ledger_path)
        ledger.write(new_job(
            "job-auto-001",
            execution_policy=ExecutionPolicy.auto_with_budget(
                max_charge,
                permit_one_submission=True,
                permit_reference_upload=True,
            ),
        ))
        return ledger

    def _exact_request_ledger(self):
        ledger = JobLedger(self.ledger_path)
        ledger.write(new_job(
            "job-auto-001",
            execution_policy=ExecutionPolicy.auto_exact_request(
                permit_one_submission=True,
                permit_reference_upload=True,
            ),
        ))
        return ledger

    def test_explicit_exact_request_can_proceed_when_provider_has_no_quote_api(self):
        ledger = self._exact_request_ledger()
        client = FakeDesignClient({
            "status": [{"ready": True}],
            "account": [{"ready": True}],
            "quote": [{"quote_available": False}],
            "submit": [{"submit_id": "sub-exact"}],
            "query": [{"status": "succeeded", "artifact": self.result_artifact}],
        })
        result = auto_orchestrator.run_until_blocked(
            ledger, client, preview_receipt=_preview(self.preview_path), request=_request()
        )
        self.assertEqual(result.state, JobState.COMPLETED)
        self.assertEqual([name for name, _ in client.calls].count("submit"), 1)

    def test_within_budget_submits_once_and_completes(self):
        ledger = self._ledger()
        request = _request()
        request["download_dir"] = str(self.root / "downloads")
        request["approved_roots"] = [str(self.root)]
        client = FakeDesignClient({
            "status": [{"ready": True}],
            "account": [{"ready": True}],
            "quote": [{"quote_available": True, "amount": "1.00", "currency": "CNY"}],
            "submit": [{"submit_id": "sub-001"}],
            "query": [{"status": "succeeded", "artifact": self.result_artifact}],
        })
        result = auto_orchestrator.run_until_blocked(ledger, client, preview_receipt=_preview(self.preview_path), request=request)
        self.assertEqual(result.state, JobState.COMPLETED)
        self.assertEqual([name for name, _ in client.calls].count("submit"), 1)
        self.assertEqual(ledger.read()["submit"]["design_submit_id"], "sub-001")
        self.assertEqual(ledger.read()["result"]["sha256"], self.result_artifact["sha256"])
        query_arguments = next(arguments for name, arguments in client.calls if name == "query")
        self.assertEqual(query_arguments["download_dir"], str(self.root / "downloads"))
        self.assertEqual(query_arguments["approved_roots"], [str(self.root)])

    def test_numeric_budget_without_authoritative_quote_stops_before_submit(self):
        ledger = self._ledger()
        client = FakeDesignClient({
            "status": [{"ready": True}],
            "account": [{"ready": True}],
            "quote": [{"quote_available": False}],
        })
        result = auto_orchestrator.run_until_blocked(ledger, client, preview_receipt=_preview(self.preview_path), request=_request())
        self.assertEqual(result.blocked_reason, "QUOTE_UNAVAILABLE")
        self.assertNotIn("submit", [name for name, _ in client.calls])
        self.assertEqual(ledger.read()["state"], JobState.CAPABILITY_RESOLVED.value)

    def test_restart_from_submitted_queries_without_resubmitting(self):
        ledger = self._ledger()
        first = FakeDesignClient({
            "status": [{"ready": True}],
            "account": [{"ready": True}],
            "quote": [{"quote_available": True, "amount": "1.00"}],
            "submit": [{"submit_id": "sub-restart"}],
            "query": [{"status": "querying"}],
        })
        first_result = auto_orchestrator.run_until_blocked(ledger, first, preview_receipt=_preview(self.preview_path), request=_request())
        self.assertEqual(first_result.state, JobState.QUERYING)

        restarted = JobLedger(self.ledger_path)
        second = FakeDesignClient({
            "query": [{"status": "succeeded", "artifact": self.result_artifact}],
        })
        result = auto_orchestrator.run_until_blocked(restarted, second, preview_receipt=_preview(self.preview_path), request=_request())
        self.assertEqual(result.state, JobState.COMPLETED)
        self.assertEqual([name for name, _ in second.calls], ["query"])

    def test_unknown_state_is_query_only_on_resume(self):
        ledger = self._ledger()
        first = FakeDesignClient({
            "status": [{"ready": True}],
            "account": [{"ready": True}],
            "quote": [{"quote_available": True, "amount": "1.00"}],
            "submit": [{"submit_id": "sub-unknown"}],
            "query": [{"status": "unknown"}],
        })
        result = auto_orchestrator.run_until_blocked(ledger, first, preview_receipt=_preview(self.preview_path), request=_request())
        self.assertEqual(result.state, JobState.UNKNOWN)
        second = FakeDesignClient({"query": [{"status": "querying"}]})
        resumed = auto_orchestrator.run_until_blocked(JobLedger(self.ledger_path), second, preview_receipt=_preview(self.preview_path), request=_request())
        self.assertEqual(resumed.state, JobState.QUERYING)
        self.assertEqual([name for name, _ in second.calls], ["query"])


if __name__ == "__main__":
    unittest.main()
