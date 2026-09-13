from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.json_contracts import canonical_fingerprint
from scripts.video_batch_allowance import (
    AllowanceAlreadyActivatedError,
    AllowanceCommitIndeterminateError,
    BatchScopeError,
    BudgetExceededError,
    ReservationConsumedError,
    VideoBatchAllowance,
)
from scripts.video_service import build_video_request_fingerprint


class RecordingApprover:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def confirm_video_batch(self, request):
        self.requests.append(copy.deepcopy(dict(request)))
        return "native-video-batch-confirmed"


def make_quote() -> dict:
    request_1 = {
        "mode": "text2video", "prompt": "shot one", "model": "seedance-test",
        "video_resolution": "720p", "ratio": "16:9", "duration_seconds": 4,
    }
    request_2 = {**request_1, "prompt": "shot one\n\nRepair directive: Keep stable."}
    attempts = [
        {"attempt_number": 1, "repair_directive": None, "request": request_1,
         "request_fingerprint": build_video_request_fingerprint(request_1), "credit_ceiling": 7},
        {"attempt_number": 2, "repair_directive": "temporal_stability", "request": request_2,
         "request_fingerprint": build_video_request_fingerprint(request_2), "credit_ceiling": 7},
    ]
    quote = {
        "schema_version": "1.0", "quote_version": "v001",
        "project_id": "vp_" + "1" * 24, "design_version": "v001",
        "design_fingerprint": "2" * 64, "rights_receipt_id": "rr_" + "3" * 24,
        "source_sha256": "4" * 64, "analysis_version": "v001",
        "machine_fingerprint": "5" * 64, "creative_mode": "authorized_replication",
        "audio_policy": "silent", "output_destination": "/approved/output/final.mp4",
        "output_profile": {"container": "mp4", "codec": "h264", "width": 1280, "height": 720, "fps": 24},
        "capability_snapshot_fingerprint": "6" * 64,
        "cost_basis": {"kind": "operator_ceiling", "credit_ceiling": 7, "currency": "credits", "source": "operator", "recorded_at": "2026-09-14T00:00:00Z"},
        "items": [{"shot_id": "S01", "mode": "text2video", "credit_ceiling": 7,
                   "request_fingerprints": [entry["request_fingerprint"] for entry in attempts], "attempts": attempts}],
        "item_count": 1, "task_count": 2, "reserved_retry_count": 1,
        "target_total_duration_seconds": 4, "total_credit_ceiling": 14,
        "quoted_at": "2026-09-14T00:00:00Z",
    }
    quote["quote_fingerprint"] = canonical_fingerprint(quote)
    return quote


def reserve_worker(root: str, allowance_id: str, fingerprint: str, start, queue) -> None:
    start.wait()
    try:
        result = VideoBatchAllowance(Path(root)).reserve(
            allowance_id, shot_id="S01", attempt=1, request_fingerprint=fingerprint
        )
        queue.put(("reserved", result["reservation_id"]))
    except ReservationConsumedError:
        queue.put(("rejected", None))


class VideoBatchAllowanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "allowances"
        self.quote = make_quote()
        self.approver = RecordingApprover()
        self.allowances = VideoBatchAllowance(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def activate(self) -> str:
        return self.allowances.activate(self.quote, self.approver)

    def test_activation_dialog_contains_entire_exact_envelope(self) -> None:
        allowance_id = self.activate()
        displayed = self.approver.requests[-1]
        self.assertEqual(displayed["project_id"], self.quote["project_id"])
        self.assertEqual(displayed["source_sha256"], self.quote["source_sha256"])
        self.assertEqual(displayed["design_version"], "v001")
        self.assertEqual(displayed["rights_receipt_id"], self.quote["rights_receipt_id"])
        rights_bytes = json.dumps(
            {"creative_mode": "authorized_replication", "design_fingerprint": "2" * 64,
             "project_id": "vp_" + "1" * 24, "rights_receipt_id": "rr_" + "3" * 24,
             "source_sha256": "4" * 64},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(displayed["rights_binding_fingerprint"], hashlib.sha256(rights_bytes).hexdigest())
        self.assertEqual(displayed["quote_fingerprint"], self.quote["quote_fingerprint"])
        self.assertEqual(displayed["total_credit_ceiling"], 14)
        self.assertEqual(displayed["shot_count"], 1)
        self.assertEqual(displayed["destination"], self.quote["output_destination"])
        self.assertEqual(displayed["audio_policy"], "silent")
        self.assertEqual(displayed["output_profile"], self.quote["output_profile"])
        self.assertEqual(displayed["items"][0]["attempts"], self.quote["items"][0]["attempts"])
        self.assertTrue(allowance_id.startswith("ba_"))

    def test_unlisted_request_fingerprint_or_retry_is_rejected(self) -> None:
        allowance_id = self.activate()
        with self.assertRaises(BatchScopeError):
            self.allowances.reserve(allowance_id, shot_id="S01", attempt=9, request_fingerprint="0" * 64)
        with self.assertRaises(BatchScopeError):
            self.allowances.reserve(
                allowance_id, shot_id="S01", attempt=True,
                request_fingerprint=self.quote["items"][0]["request_fingerprints"][0],
            )

    def test_scope_expansion_or_persisted_tamper_invalidates_allowance(self) -> None:
        allowance_id = self.activate()
        expanded = copy.deepcopy(self.quote)
        expanded["items"][0]["attempts"][0]["request"]["duration_seconds"] += 1
        with self.assertRaises(BatchScopeError):
            self.allowances.assert_quote(allowance_id, expanded)
        allowance_path = self.root / "allowances" / f"{allowance_id}.json"
        stored = json.loads(allowance_path.read_text())
        stored["total_credit_ceiling"] += 1
        allowance_path.write_text(json.dumps(stored))
        with self.assertRaises(BatchScopeError):
            self.allowances.get(allowance_id)

    def test_declared_total_cannot_be_lower_than_sum_of_request_ceilings(self) -> None:
        corrupt = copy.deepcopy(self.quote)
        corrupt["total_credit_ceiling"] -= 1
        corrupt["quote_fingerprint"] = canonical_fingerprint({k: v for k, v in corrupt.items() if k != "quote_fingerprint"})
        with self.assertRaises(BudgetExceededError):
            self.allowances.activate(corrupt, self.approver)
        self.assertEqual(self.approver.requests, [])

    def test_parallel_reservation_for_same_attempt_has_one_winner(self) -> None:
        allowance_id = self.activate()
        ctx = multiprocessing.get_context("spawn")
        start, queue = ctx.Event(), ctx.Queue()
        fingerprint = self.quote["items"][0]["request_fingerprints"][0]
        processes = [ctx.Process(target=reserve_worker, args=(str(self.root), allowance_id, fingerprint, start, queue)) for _ in range(2)]
        for process in processes:
            process.start()
        start.set()
        results = [queue.get(timeout=10)[0] for _ in processes]
        for process in processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(results.count("reserved"), 1)
        self.assertEqual(results.count("rejected"), 1)

    def test_ambiguous_attempt_remains_consumed_and_commit_binds_submit_id(self) -> None:
        allowance_id = self.activate()
        fingerprint = self.quote["items"][0]["request_fingerprints"][0]
        reservation = self.allowances.reserve(allowance_id, shot_id="S01", attempt=1, request_fingerprint=fingerprint)
        ambiguous = self.allowances.mark_ambiguous(reservation["reservation_id"], "TIMEOUT_AFTER_INVOKE")
        self.assertEqual(ambiguous["state"], "ambiguous")
        with self.assertRaises(ReservationConsumedError):
            self.allowances.reserve(allowance_id, shot_id="S01", attempt=1, request_fingerprint=fingerprint)

        retry_fp = self.quote["items"][0]["request_fingerprints"][1]
        retry = self.allowances.reserve(allowance_id, shot_id="S01", attempt=2, request_fingerprint=retry_fp)
        committed = self.allowances.commit(retry["reservation_id"], "submit_opaque_1")
        self.assertEqual(committed["submit_id"], "submit_opaque_1")
        with self.assertRaises(ReservationConsumedError):
            self.allowances.commit(retry["reservation_id"], "different")

    def test_submit_and_error_identifiers_are_opaque_not_paths_or_secrets(self) -> None:
        allowance_id = self.activate()
        fingerprint = self.quote["items"][0]["request_fingerprints"][0]
        reservation = self.allowances.reserve(allowance_id, shot_id="S01", attempt=1, request_fingerprint=fingerprint)
        with self.assertRaises(BatchScopeError):
            self.allowances.commit(reservation["reservation_id"], "../../account.json")
        with self.assertRaises(BatchScopeError):
            self.allowances.mark_ambiguous(reservation["reservation_id"], "token=secret")

    def test_allowance_survives_restart_but_quote_cannot_be_reactivated(self) -> None:
        allowance_id = self.activate()
        restarted = VideoBatchAllowance(self.root)
        self.assertEqual(restarted.get(allowance_id)["state"], "active")
        with self.assertRaises(AllowanceAlreadyActivatedError):
            restarted.activate(self.quote, self.approver)

    def test_private_permissions_and_literal_credit_accounting(self) -> None:
        allowance_id = self.activate()
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        allowance_path = self.root / "allowances" / f"{allowance_id}.json"
        self.assertEqual(allowance_path.stat().st_mode & 0o777, 0o600)
        for item in self.quote["items"]:
            for attempt in item["attempts"]:
                self.allowances.reserve(allowance_id, shot_id=item["shot_id"], attempt=attempt["attempt_number"], request_fingerprint=attempt["request_fingerprint"])
        state = self.allowances.get(allowance_id)
        self.assertEqual(state["consumed_credits"], 14)
        self.assertLessEqual(state["consumed_credits"], state["total_credit_ceiling"])

    def test_allowance_with_broadened_file_mode_is_rejected(self) -> None:
        allowance_id = self.activate()
        allowance_path = self.root / "allowances" / f"{allowance_id}.json"
        allowance_path.chmod(0o644)
        with self.assertRaises(BatchScopeError):
            self.allowances.get(allowance_id)

    def test_duplicate_request_fingerprint_across_shots_is_rejected_before_approval(self) -> None:
        corrupt = copy.deepcopy(self.quote)
        duplicate = copy.deepcopy(corrupt["items"][0])
        duplicate["shot_id"] = "S02"
        corrupt["items"].append(duplicate)
        corrupt["item_count"] = 2
        corrupt["task_count"] = 4
        corrupt["reserved_retry_count"] = 2
        corrupt["target_total_duration_seconds"] = 8
        corrupt["total_credit_ceiling"] = 28
        corrupt["quote_fingerprint"] = canonical_fingerprint(
            {key: value for key, value in corrupt.items() if key != "quote_fingerprint"}
        )
        with self.assertRaises(BatchScopeError):
            self.allowances.activate(corrupt, self.approver)
        self.assertEqual(self.approver.requests, [])

    def test_wrong_native_confirmation_token_does_not_activate_or_persist(self) -> None:
        class WrongApprover:
            calls = 0

            def confirm_video_batch(inner, request):
                inner.calls += 1
                return "native-user-confirmed"

        approver = WrongApprover()
        with self.assertRaises(BatchScopeError):
            self.allowances.activate(self.quote, approver)
        self.assertEqual(approver.calls, 1)
        self.assertEqual(list((self.root / "allowances").glob("*.json")), [])
        self.assertEqual(list((self.root / "activations").glob("*.json")), [])

    def test_submit_id_cannot_be_bound_to_two_reservations(self) -> None:
        allowance_id = self.activate()
        first, second = self.quote["items"][0]["attempts"]
        reservation_1 = self.allowances.reserve(
            allowance_id, shot_id="S01", attempt=1,
            request_fingerprint=first["request_fingerprint"],
        )
        reservation_2 = self.allowances.reserve(
            allowance_id, shot_id="S01", attempt=2,
            request_fingerprint=second["request_fingerprint"],
        )
        self.allowances.commit(reservation_1["reservation_id"], "submit_same")
        with self.assertRaises(ReservationConsumedError):
            self.allowances.commit(reservation_2["reservation_id"], "submit_same")

    def test_post_replace_durability_failure_is_indeterminate_and_reservation_stays_consumed(self) -> None:
        allowance_id = self.activate()
        attempt = self.quote["items"][0]["attempts"][0]
        real_fsync = os.fsync

        def fail_directory_fsync(descriptor: int) -> None:
            if os.path.isdir(f"/dev/fd/{descriptor}"):
                raise OSError("injected directory fsync failure")
            real_fsync(descriptor)

        with patch("scripts.video_batch_allowance.os.fsync", side_effect=fail_directory_fsync):
            with self.assertRaises(AllowanceCommitIndeterminateError) as raised:
                self.allowances.reserve(
                    allowance_id, shot_id="S01", attempt=1,
                    request_fingerprint=attempt["request_fingerprint"],
                )
        self.assertEqual(raised.exception.allowance_id, allowance_id)
        restarted = VideoBatchAllowance(self.root)
        with self.assertRaises(ReservationConsumedError):
            restarted.reserve(
                allowance_id, shot_id="S01", attempt=1,
                request_fingerprint=attempt["request_fingerprint"],
            )


if __name__ == "__main__":
    unittest.main()
