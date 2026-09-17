"""Tests for scripts/job_ledger.py.

The ledger enforces the spec's state machine:
  Draft -> DccSelected -> PreviewSpecified -> PreviewValidated ->
  CapabilityResolved -> Quoted -> Approved -> Submitted -> Querying ->
  Completed | Failed | Unknown

Anything that skips a gate is rejected. Any change to preview hash, prompt,
reference, model, resolution, ratio, or duration invalidates Quoted/Approved.
Updates are atomic (write-temp + fsync + replace) with a monotonic revision.
Secrets must never be stored.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "seedance_pipeline"))

from job_ledger import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    BudgetExceededError,
    ExecutionMode,
    ExecutionPolicy,
    InvalidTransitionError,
    JobLedger,
    JobState,
    QuoteInputs,
    StaleReceiptError,
    load_ledger,
    new_job,
)

VALID_PROGRESS_PATH = [
    JobState.DRAFT,
    JobState.DCC_SELECTED,
    JobState.PREVIEW_SPECIFIED,
    JobState.PREVIEW_VALIDATED,
    JobState.CAPABILITY_RESOLVED,
    JobState.QUOTED,
    JobState.APPROVED,
    JobState.SUBMITTED,
    JobState.QUERYING,
    JobState.COMPLETED,
]


def _drive_to(ledger: JobLedger, target: JobState) -> None:
    job = ledger.read()
    if job["state"] == target:
        return
    quote = QuoteInputs(
        prompt="a render of the scene",
        model="seedance-2.5",
        resolution="1280x720",
        ratio="16:9",
        duration_seconds=4.0,
        reference_artifact_ids=[],
    )
    preview = {
        "artifact_id": "blender_preview_abc123",
        "sha256": "0" * 64,
        "producer_plugin": "codex-blender",
        "producer_version": "0.1.0",
    }

    if JobState.DRAFT not in (job["state"], target):
        ledger.write(new_job(job["job_id"]))
        job = ledger.read()

    sequence = [
        (JobState.DCC_SELECTED, {"selected_companion": {"plugin_id": "codex-blender", "version": "0.1.0", "contract_version": "1.0.0"}}),
        (JobState.PREVIEW_SPECIFIED, {"preview": preview}),
        (JobState.PREVIEW_VALIDATED, {"preview_hash_validated": True}),
        (JobState.CAPABILITY_RESOLVED, {"resolved_capability": {"model": "seedance-2.5"}}),
        (JobState.QUOTED, {"quote_inputs": quote, "quote": {"price_usd": 1.0}}),
        (JobState.APPROVED, {"approved_by": "user", "approved_at": "2026-09-12T00:00:00Z"}),
        (JobState.SUBMITTED, {"submit": {"design_submit_id": "ds_123", "submitted_at": "2026-09-12T00:00:01Z", "design_status": "submitted"}}),
        (JobState.QUERYING, {}),
        (JobState.COMPLETED, {"result": {"artifact_id": "result_xyz", "sha256": "1" * 64, "path": "/tmp/out.mp4"}}),
    ]
    order = [s for s, _ in sequence]
    target_index = order.index(target)
    for state, fields in sequence[: target_index + 1]:
        if ledger.read()["state"] != state.value:
            ledger.transition(state, **fields)


class JobStateEnumTests(unittest.TestCase):
    def test_states_match_spec(self) -> None:
        expected = {
            "Draft", "DccSelected", "PreviewSpecified", "PreviewValidated",
            "CapabilityResolved", "Quoted", "Approved", "Submitted",
            "Querying", "Completed", "Failed", "Unknown",
        }
        self.assertEqual({s.value for s in JobState}, expected)


class LedgerSchemaTests(unittest.TestCase):
    def test_new_and_completed_jobs_match_the_closed_schema(self) -> None:
        from jsonschema import Draft7Validator

        schema = json.loads((ROOT / "schemas" / "3d_job.schema.json").read_text())
        validator = Draft7Validator(schema)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = JobLedger(Path(tmp) / "job.json")
            ledger.write(new_job("job-schema-001"))
            self.assertEqual(list(validator.iter_errors(ledger.read())), [])
            _drive_to(ledger, JobState.COMPLETED)
            self.assertEqual(list(validator.iter_errors(ledger.read())), [])


class TransitionTests(unittest.TestCase):
    def test_allowed_path_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            _drive_to(ledger, JobState.COMPLETED)
            self.assertEqual(ledger.read()["state"], JobState.COMPLETED)

    def test_skipping_a_gate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            with self.assertRaises(InvalidTransitionError):
                ledger.transition(JobState.QUOTED)

    def test_jumping_directly_to_submitted_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            ledger.transition(JobState.DCC_SELECTED, selected_companion={"plugin_id": "codex-blender", "version": "0.1.0", "contract_version": "1.0.0"})
            with self.assertRaises(InvalidTransitionError):
                ledger.transition(JobState.SUBMITTED)

    def test_failed_and_unknown_are_terminal_except_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            ledger.transition(JobState.FAILED, error_category="timeout")
            with self.assertRaises(InvalidTransitionError):
                ledger.transition(JobState.COMPLETED)

    def test_revision_is_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            r0 = ledger.read()["revision"]
            ledger.transition(JobState.DCC_SELECTED, selected_companion={"plugin_id": "codex-blender", "version": "0.1.0", "contract_version": "1.0.0"})
            r1 = ledger.read()["revision"]
            self.assertGreater(r1, r0)


class ExecutionPolicyTests(unittest.TestCase):
    def test_exact_request_policy_has_no_synthetic_budget(self) -> None:
        policy = ExecutionPolicy.auto_exact_request(
            permit_one_submission=True,
            permit_reference_upload=True,
        )
        self.assertEqual(policy.mode, ExecutionMode.AUTO_EXACT_REQUEST)
        self.assertIsNone(policy.max_charge)
        self.assertTrue(policy.permit_unquoted_exact_request)

    def test_policy_cannot_change_after_quote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = JobLedger(Path(tmp) / "job.json")
            policy = ExecutionPolicy.auto_with_budget("3.20", True, True)
            ledger.write(new_job("job-immutable", execution_policy=policy))
            _drive_to(ledger, JobState.CAPABILITY_RESOLVED)
            quote = QuoteInputs("p", "seedance-2.5", "1280x720", "16:9", 4.0)
            ledger.record_quote(quote, {"amount": "3.00"})
            tampered = ledger.read()
            tampered["execution_policy"]["max_charge"] = "99.00"
            with self.assertRaises(InvalidTransitionError):
                ledger.write(tampered)

    def test_auto_policy_stops_quote_that_exceeds_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = JobLedger(Path(tmp) / "job.json")
            policy = ExecutionPolicy.auto_with_budget("3.20", permit_one_submission=True, permit_reference_upload=True)
            ledger.write(new_job("job-1", execution_policy=policy))
            _drive_to(ledger, JobState.CAPABILITY_RESOLVED)
            quote = QuoteInputs("p", "seedance-2.5", "1280x720", "16:9", 4.0)
            with self.assertRaises(BudgetExceededError):
                ledger.record_quote(quote, {"amount": "3.21", "currency": "CNY"})
            self.assertEqual(ledger.read()["state"], JobState.CAPABILITY_RESOLVED.value)

    def test_auto_policy_records_within_cap_quote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = JobLedger(Path(tmp) / "job.json")
            policy = ExecutionPolicy.auto_with_budget("3.20", permit_one_submission=True, permit_reference_upload=True)
            ledger.write(new_job("job-1", execution_policy=policy))
            _drive_to(ledger, JobState.CAPABILITY_RESOLVED)
            quote = QuoteInputs("p", "seedance-2.5", "1280x720", "16:9", 4.0)
            ledger.record_quote(quote, {"amount": "3.20", "currency": "CNY"})
            result = ledger.read()
            self.assertEqual(result["state"], JobState.QUOTED.value)
            self.assertEqual(result["execution_policy"]["mode"], "auto_with_budget")

    def test_policy_cap_is_decimal_and_non_negative(self) -> None:
        self.assertEqual(ExecutionPolicy.auto_with_budget(Decimal("0"), True, False).max_charge, Decimal("0"))
        with self.assertRaises(ValueError):
            ExecutionPolicy.auto_with_budget("-0.01", True, False)


class QuoteInvalidationTests(unittest.TestCase):
    def test_changing_prompt_invalidates_quoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            _drive_to(ledger, JobState.QUOTED)
            ledger.invalidate_quote(reason="prompt changed")
            self.assertEqual(ledger.read()["state"], JobState.PREVIEW_VALIDATED)

    def test_changing_preview_hash_invalidates_quoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            _drive_to(ledger, JobState.QUOTED)
            ledger.invalidate_quote(reason="preview hash changed", new_preview_hash="f" * 64)
            job = ledger.read()
            self.assertEqual(job["state"], JobState.PREVIEW_SPECIFIED)
            self.assertEqual(job["preview"]["sha256"], "f" * 64)

    def test_change_after_approved_invalidates_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            _drive_to(ledger, JobState.APPROVED)
            ledger.invalidate_quote(reason="duration changed")
            self.assertEqual(ledger.read()["state"], JobState.PREVIEW_VALIDATED)


class AtomicityTests(unittest.TestCase):
    def test_corrupt_ledger_is_detected_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger_file.write_text("{not json")
            with self.assertRaises(json.JSONDecodeError):
                load_ledger(ledger_file)

    def test_truncated_ledger_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger_file.write_text('{"schema_version": "1.0.0", "job_id":')
            with self.assertRaises(json.JSONDecodeError):
                load_ledger(ledger_file)

    def test_restart_loads_existing_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            first = JobLedger(ledger_file)
            first.write(new_job("job-1"))
            first.transition(JobState.DCC_SELECTED, selected_companion={"plugin_id": "codex-blender", "version": "0.1.0", "contract_version": "1.0.0"})
            second = JobLedger(ledger_file)
            job = second.read()
            self.assertEqual(job["state"], JobState.DCC_SELECTED)
            self.assertEqual(job["job_id"], "job-1")

    def test_concurrent_writes_serialize_via_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            errors: list[Exception] = []

            def hammer() -> None:
                try:
                    for _ in range(20):
                        ledger.transition(JobState.FAILED, error_category="concurrent")
                        ledger.write(new_job("job-1"))
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=hammer) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            # Final state is well-formed JSON and one of the legal states.
            self.assertTrue(ledger_file.is_file())
            job = load_ledger(ledger_file)
            self.assertIn(job["state"], {s.value for s in JobState})


class NoSecretsTests(unittest.TestCase):
    SECRET_KEYS = {"token", "api_key", "password", "secret", "authorization", "credit_card"}

    def _walk(self, value):
        if isinstance(value, dict):
            for k, v in value.items():
                self.assertNotIn(k.lower(), self.SECRET_KEYS, f"secret-like key {k!r} present")
                self._walk(v)
        elif isinstance(value, list):
            for item in value:
                self._walk(item)

    def test_ledger_never_stores_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            _drive_to(ledger, JobState.COMPLETED)
            raw = ledger_file.read_text()
            self._walk(json.loads(raw))
            for key in self.SECRET_KEYS:
                self.assertNotIn(key, raw.lower())


class StaleReceiptReferenceTests(unittest.TestCase):
    def test_invalidate_quote_with_unknown_hash_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = Path(tmp) / "job.json"
            ledger = JobLedger(ledger_file)
            ledger.write(new_job("job-1"))
            _drive_to(ledger, JobState.QUOTED)
            with self.assertRaises(StaleReceiptError):
                ledger.invalidate_quote(reason="stale reference", new_preview_hash="not-a-hash")


class TransitionsTableTests(unittest.TestCase):
    def test_allowed_transitions_table(self) -> None:
        for source, targets in ALLOWED_TRANSITIONS.items():
            for target in targets:
                self.assertIsInstance(source, JobState)
                self.assertIsInstance(target, JobState)


if __name__ == "__main__":
    unittest.main()
