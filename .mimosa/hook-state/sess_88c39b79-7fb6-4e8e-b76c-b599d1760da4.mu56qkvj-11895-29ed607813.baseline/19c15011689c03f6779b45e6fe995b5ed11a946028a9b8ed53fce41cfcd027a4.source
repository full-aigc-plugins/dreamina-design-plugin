from __future__ import annotations

import copy
import json
import multiprocessing
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.json_contracts import ContractValidationError, canonical_fingerprint
from scripts.video_batch_allowance import (
    AllowanceAlreadyActivatedError,
    AllowanceCommitIndeterminateError,
    BatchScopeError,
    BudgetExceededError,
    ReservationConsumedError,
    FileSealKeyStore,
    LocalQuoteResolver,
    SealKeyUnavailableError,
    VideoBatchAllowance,
)
from scripts.video_service import build_video_request_fingerprint
from scripts.video_generation_planner import validate_batch_quote


class RecordingApprover:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def confirm_video_batch(self, request):
        self.requests.append(copy.deepcopy(dict(request)))
        return "native-video-batch-confirmed"


RIGHTS_RECEIPT = {
    "receipt_id": "rr_" + "3" * 24,
    "project_id": "vp_" + "1" * 24,
    "source_sha256": "4" * 64,
    "creative_mode": "authorized_replication",
    "design_fingerprint": "2" * 64,
    "evidence": "persisted-rights",
}


class FakeProjectStore:
    def find_version_by_field(self, *args, **kwargs):
        return copy.deepcopy(RIGHTS_RECEIPT)

    def read_version(self, *args, **kwargs):
        return {
            "project_id": args[0], "version": args[2],
            "design_fingerprint": "2" * 64, "source_sha256": "4" * 64,
            "analysis_version": "v001", "payload": {
            "preserve": ["timing"], "required_media": ["video"],
            "purpose": "test", "audience": "test", "territory": "test",
        }}


class FakeRightsService:
    def assert_scope(self, receipt, *, required, binding):
        if receipt != RIGHTS_RECEIPT or required != {"timing"}:
            raise PermissionError("rights mismatch")


def make_allowances(root: Path, key_path: Path, **kwargs) -> VideoBatchAllowance:
    kwargs.setdefault("now", lambda: datetime(2026, 9, 14, 1, tzinfo=timezone.utc))
    return VideoBatchAllowance(
        root, seal_key_store=FileSealKeyStore(key_path),
        project_store=FakeProjectStore(), rights_service=FakeRightsService(), **kwargs,
    )


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
        "rights_receipt_fingerprint": canonical_fingerprint(RIGHTS_RECEIPT),
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


def reserve_worker(root: str, key_path: str, allowance_id: str, fingerprint: str, start, queue) -> None:
    start.wait()
    try:
        result = make_allowances(Path(root), Path(key_path)).reserve(
            allowance_id, shot_id="S01", attempt=1, request_fingerprint=fingerprint
        )
        queue.put(("reserved", result["reservation_id"]))
    except ReservationConsumedError:
        queue.put(("rejected", None))


def reserve_any_worker(root: str, key_path: str, allowance_id: str, fingerprint: str, start, queue) -> None:
    start.wait()
    try:
        result = make_allowances(Path(root), Path(key_path)).reserve(
            allowance_id, shot_id="S01", attempt=1, request_fingerprint=fingerprint
        )
        queue.put(("reserved", result["allowance_id"]))
    except ReservationConsumedError:
        queue.put(("rejected", allowance_id))


def terminal_worker(root: str, key_path: str, reservation_id: str, state: str, submit_id: str, start, queue) -> None:
    allowances = make_allowances(Path(root), Path(key_path))
    start.wait()
    try:
        result = (
            allowances.commit(reservation_id, submit_id)
            if state == "committed"
            else allowances.mark_ambiguous(
                reservation_id, "TIMEOUT_AFTER_INVOKE", submit_id=submit_id
            )
        )
        queue.put(("transitioned", result["state"]))
    except ReservationConsumedError:
        queue.put(("rejected", None))
    finally:
        allowances.close()


class VideoBatchAllowanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "allowances"
        self.key_path = Path(self.temp.name) / "config" / "allowance-seal.key"
        self.quote = make_quote()
        self.approver = RecordingApprover()
        self.allowances = make_allowances(self.root, self.key_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def activate(self) -> str:
        return self.allowances.activate(self.quote, self.approver)

    def activate_distinct_original(self, digit: str = "9") -> tuple[str, dict]:
        quote = copy.deepcopy(self.quote)
        quote["project_id"] = "vp_" + digit * 24
        quote["creative_mode"] = "original_redesign"
        quote.pop("rights_receipt_id")
        quote.pop("rights_receipt_fingerprint")
        quote["items"][0]["shot_id"] = f"S{digit}{digit}"
        for index, attempt in enumerate(quote["items"][0]["attempts"], start=1):
            attempt["request"]["prompt"] += f" distinct-{digit}-{index}"
            attempt["request_fingerprint"] = build_video_request_fingerprint(attempt["request"])
        quote["items"][0]["request_fingerprints"] = [
            attempt["request_fingerprint"] for attempt in quote["items"][0]["attempts"]
        ]
        quote["quote_fingerprint"] = canonical_fingerprint(
            {key: value for key, value in quote.items() if key != "quote_fingerprint"}
        )
        return self.allowances.activate(quote, self.approver), quote

    def reserve_first(self, allowance_id: str, quote: dict) -> dict:
        attempt = quote["items"][0]["attempts"][0]
        return self.allowances.reserve(
            allowance_id, shot_id=quote["items"][0]["shot_id"], attempt=1,
            request_fingerprint=attempt["request_fingerprint"],
        )

    def test_activation_dialog_contains_entire_exact_envelope(self) -> None:
        allowance_id = self.activate()
        displayed = self.approver.requests[-1]
        self.assertEqual(displayed["project_id"], self.quote["project_id"])
        self.assertEqual(displayed["source_sha256"], self.quote["source_sha256"])
        self.assertEqual(displayed["design_version"], "v001")
        self.assertEqual(displayed["rights_receipt_id"], self.quote["rights_receipt_id"])
        self.assertEqual(displayed["rights_receipt_fingerprint"], canonical_fingerprint(RIGHTS_RECEIPT))
        self.assertEqual(displayed["quote_fingerprint"], self.quote["quote_fingerprint"])
        self.assertEqual(displayed["total_credit_ceiling"], 14)
        self.assertEqual(displayed["shot_count"], 1)
        self.assertEqual(displayed["destination"], self.quote["output_destination"])
        self.assertEqual(displayed["audio_policy"], "silent")
        self.assertEqual(displayed["output_profile"], self.quote["output_profile"])
        self.assertEqual(displayed["cost_basis"], self.quote["cost_basis"])
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
        processes = [ctx.Process(target=reserve_worker, args=(str(self.root), str(self.key_path), allowance_id, fingerprint, start, queue)) for _ in range(2)]
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

    def test_ambiguous_state_requires_query_and_preserves_exact_invocation_identity(self) -> None:
        allowance_id = self.activate()
        attempt = self.quote["items"][0]["attempts"][0]
        reservation = self.allowances.reserve(
            allowance_id, shot_id="S01", attempt=1,
            request_fingerprint=attempt["request_fingerprint"],
        )
        ambiguous = self.allowances.mark_ambiguous(
            reservation["reservation_id"], "TIMEOUT_AFTER_INVOKE", submit_id="submit_known_1"
        )
        self.assertEqual(ambiguous["required_action"], "query")
        self.assertEqual(ambiguous["allowance_id"], allowance_id)
        self.assertEqual(ambiguous["shot_id"], "S01")
        self.assertEqual(ambiguous["attempt"], 1)
        self.assertEqual(ambiguous["request_fingerprint"], attempt["request_fingerprint"])
        self.assertEqual(ambiguous["submit_id"], "submit_known_1")

    def test_ambiguous_without_submit_id_requires_manual_review(self) -> None:
        allowance_id = self.activate()
        reservation = self.reserve_first(allowance_id, self.quote)
        ambiguous = self.allowances.mark_ambiguous(
            reservation["reservation_id"], "TIMEOUT_AFTER_INVOKE"
        )
        self.assertEqual(ambiguous["required_action"], "manual_review")
        self.assertNotIn("submit_id", ambiguous)
        with self.assertRaises(ReservationConsumedError):
            self.reserve_first(allowance_id, self.quote)

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
        restarted = make_allowances(self.root, self.key_path)
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
        self.assertEqual(state["cost_basis"], self.quote["cost_basis"])
        self.assertEqual(state["quote_identity"]["project_id"], self.quote["project_id"])
        self.assertNotIn("state_fingerprint", state)
        self.assertEqual(len(state["seal"]), 64)
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

    def test_mutating_native_approver_cannot_change_confirmed_envelope(self) -> None:
        class MutatingApprover:
            def confirm_video_batch(inner, request):
                request["total_credit_ceiling"] = 1
                return "native-video-batch-confirmed"

        with self.assertRaises(BatchScopeError):
            self.allowances.activate(self.quote, MutatingApprover())
        self.assertEqual(list((self.root / "allowances").glob("*.json")), [])

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
        restarted = make_allowances(self.root, self.key_path)
        with self.assertRaises(ReservationConsumedError):
            restarted.reserve(
                allowance_id, shot_id="S01", attempt=1,
                request_fingerprint=attempt["request_fingerprint"],
            )

    def test_allowance_tamper_cannot_be_forged_with_old_unkeyed_fingerprint(self) -> None:
        allowance_id = self.activate()
        path = self.root / "allowances" / f"{allowance_id}.json"
        stored = json.loads(path.read_text())
        stored["requests"][0]["credit_ceiling"] = 1
        stored["total_credit_ceiling"] = 1
        stored["cost_basis"]["credit_ceiling"] = 1
        stored["state_fingerprint"] = canonical_fingerprint(
            {key: value for key, value in stored.items() if key not in {"seal", "state_fingerprint"}}
        )
        path.write_text(json.dumps(stored))
        path.chmod(0o600)
        with self.assertRaises(BatchScopeError):
            self.allowances.get(allowance_id)

    def test_correctly_sealed_state_is_rejected_when_trusted_quote_mismatches(self) -> None:
        class MutableResolver:
            def __init__(inner, quote):
                inner.quote = copy.deepcopy(quote)

            def persist(inner, quote):
                inner.quote = copy.deepcopy(quote)

            def resolve(inner, identity):
                return copy.deepcopy(inner.quote)

        resolver = MutableResolver(self.quote)
        allowances = make_allowances(self.root, self.key_path, quote_resolver=resolver)
        allowance_id = allowances.activate(self.quote, self.approver)
        resolver.quote["cost_basis"]["credit_ceiling"] = 8
        resolver.quote["total_credit_ceiling"] = 16
        resolver.quote["items"][0]["credit_ceiling"] = 8
        for attempt in resolver.quote["items"][0]["attempts"]:
            attempt["credit_ceiling"] = 8
        resolver.quote["quote_fingerprint"] = canonical_fingerprint(
            {key: value for key, value in resolver.quote.items() if key != "quote_fingerprint"}
        )
        with self.assertRaises(BatchScopeError):
            allowances.get(allowance_id)

    def test_missing_or_changed_seal_key_blocks_recovery_and_reactivation(self) -> None:
        allowance_id = self.activate()
        self.key_path.unlink()
        restarted = make_allowances(self.root, self.key_path)
        with self.assertRaises(SealKeyUnavailableError):
            restarted.get(allowance_id)
        with self.assertRaises(SealKeyUnavailableError):
            restarted.activate(self.quote, self.approver)
        self.key_path.write_bytes(b"x" * 32)
        self.key_path.chmod(0o600)
        with self.assertRaises(SealKeyUnavailableError):
            restarted.get(allowance_id)

    def test_cost_basis_tamper_is_rejected_and_exact_quote_is_rechecked_on_reserve(self) -> None:
        allowance_id = self.activate()
        path = self.root / "allowances" / f"{allowance_id}.json"
        stored = json.loads(path.read_text())
        stored["cost_basis"]["source"] = "attacker"
        path.write_text(json.dumps(stored))
        path.chmod(0o600)
        with self.assertRaises(BatchScopeError):
            self.allowances.reserve(
                allowance_id, shot_id="S01", attempt=1,
                request_fingerprint=self.quote["items"][0]["request_fingerprints"][0],
            )

    def test_parallel_replay_across_two_allowances_has_one_global_winner(self) -> None:
        first_id = self.activate()
        second_quote = copy.deepcopy(self.quote)
        second_quote["project_id"] = "vp_" + "9" * 24
        second_quote["creative_mode"] = "original_redesign"
        second_quote.pop("rights_receipt_id")
        second_quote.pop("rights_receipt_fingerprint")
        second_quote["quote_fingerprint"] = canonical_fingerprint(
            {key: value for key, value in second_quote.items() if key != "quote_fingerprint"}
        )
        second_id = self.allowances.activate(second_quote, self.approver)
        fingerprint = self.quote["items"][0]["request_fingerprints"][0]
        ctx = multiprocessing.get_context("spawn")
        start, queue = ctx.Event(), ctx.Queue()
        processes = [
            ctx.Process(target=reserve_any_worker, args=(str(self.root), str(self.key_path), allowance_id, fingerprint, start, queue))
            for allowance_id in (first_id, second_id)
        ]
        for process in processes:
            process.start()
        start.set()
        results = [queue.get(timeout=10)[0] for _ in processes]
        for process in processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(results.count("reserved"), 1)
        self.assertEqual(results.count("rejected"), 1)

    def test_replacing_pinned_root_blocks_all_future_operations(self) -> None:
        allowance_id = self.activate()
        moved = Path(self.temp.name) / "moved-root"
        self.root.rename(moved)
        self.root.mkdir(mode=0o700)
        with self.assertRaises(BatchScopeError):
            self.allowances.get(allowance_id)

    def test_submit_id_is_unique_across_committed_and_ambiguous_allowances(self) -> None:
        first_id = self.activate()
        second_id, second_quote = self.activate_distinct_original()
        first = self.reserve_first(first_id, self.quote)
        second = self.reserve_first(second_id, second_quote)
        self.allowances.commit(first["reservation_id"], "submit_global")
        with self.assertRaises(ReservationConsumedError):
            self.allowances.mark_ambiguous(
                second["reservation_id"], "TIMEOUT_AFTER_INVOKE", submit_id="submit_global"
            )

        third_id, third_quote = self.activate_distinct_original("8")
        third = self.reserve_first(third_id, third_quote)
        self.allowances.mark_ambiguous(
            second["reservation_id"], "TIMEOUT_AFTER_INVOKE", submit_id="submit_ambiguous"
        )
        with self.assertRaises(ReservationConsumedError):
            self.allowances.commit(third["reservation_id"], "submit_ambiguous")
        with self.assertRaises(ReservationConsumedError):
            self.allowances.mark_ambiguous(
                third["reservation_id"], "TIMEOUT_AFTER_INVOKE", submit_id="submit_ambiguous"
            )

    def test_same_terminal_transition_is_idempotent_only_when_all_details_match(self) -> None:
        allowance_id = self.activate()
        reservation = self.reserve_first(allowance_id, self.quote)
        first = self.allowances.mark_ambiguous(
            reservation["reservation_id"], "TIMEOUT_AFTER_INVOKE", submit_id="submit_exact"
        )
        replay = self.allowances.mark_ambiguous(
            reservation["reservation_id"], "TIMEOUT_AFTER_INVOKE", submit_id="submit_exact"
        )
        self.assertEqual(replay, first)
        with self.assertRaises(ReservationConsumedError):
            self.allowances.mark_ambiguous(
                reservation["reservation_id"], "DIFFERENT_ERROR", submit_id="submit_exact"
            )

    def test_parallel_cross_allowance_terminal_submit_id_has_one_winner(self) -> None:
        first_id = self.activate()
        second_id, second_quote = self.activate_distinct_original()
        first = self.reserve_first(first_id, self.quote)
        second = self.reserve_first(second_id, second_quote)
        ctx = multiprocessing.get_context("spawn")
        start, queue = ctx.Event(), ctx.Queue()
        processes = [
            ctx.Process(target=terminal_worker, args=(str(self.root), str(self.key_path), first["reservation_id"], "committed", "submit_race", start, queue)),
            ctx.Process(target=terminal_worker, args=(str(self.root), str(self.key_path), second["reservation_id"], "ambiguous", "submit_race", start, queue)),
        ]
        for process in processes:
            process.start()
        start.set()
        results = [queue.get(timeout=10)[0] for _ in processes]
        for process in processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(results.count("transitioned"), 1)
        self.assertEqual(results.count("rejected"), 1)

    def test_rights_expiring_during_native_dialog_prevents_activation(self) -> None:
        class ExpiringRights(FakeRightsService):
            expired = False

            def assert_scope(inner, receipt, *, required, binding):
                super().assert_scope(receipt, required=required, binding=binding)
                if inner.expired:
                    raise PermissionError("rights expired")

        rights = ExpiringRights()

        class AdvancingApprover(RecordingApprover):
            def confirm_video_batch(inner, request):
                token = super().confirm_video_batch(request)
                rights.expired = True
                return token

        allowances = VideoBatchAllowance(
            self.root, seal_key_store=FileSealKeyStore(self.key_path),
            project_store=FakeProjectStore(), rights_service=rights,
        )
        with self.assertRaises(BatchScopeError):
            allowances.activate(self.quote, AdvancingApprover())
        self.assertEqual(list((self.root / "allowances").glob("*.json")), [])
        self.assertEqual(list((self.root / "activations").glob("*.json")), [])

    def test_rights_receipt_replacement_during_native_dialog_prevents_activation(self) -> None:
        class MutableStore(FakeProjectStore):
            receipt = copy.deepcopy(RIGHTS_RECEIPT)

            def find_version_by_field(inner, *args, **kwargs):
                return copy.deepcopy(inner.receipt)

        store = MutableStore()

        class ReplacingApprover(RecordingApprover):
            def confirm_video_batch(inner, request):
                token = super().confirm_video_batch(request)
                store.receipt["evidence"] = "replaced"
                return token

        allowances = VideoBatchAllowance(
            self.root, seal_key_store=FileSealKeyStore(self.key_path),
            project_store=store, rights_service=FakeRightsService(),
        )
        with self.assertRaises(BatchScopeError):
            allowances.activate(self.quote, ReplacingApprover())
        self.assertEqual(list((self.root / "allowances").glob("*.json")), [])
        self.assertEqual(list((self.root / "activations").glob("*.json")), [])

    def test_quote_replacement_during_native_dialog_prevents_activation(self) -> None:
        class MutableResolver:
            def __init__(inner):
                inner.quote = None

            def persist(inner, quote):
                inner.quote = copy.deepcopy(quote)

            def resolve(inner, identity):
                return copy.deepcopy(inner.quote)

        resolver = MutableResolver()

        class ReplacingApprover(RecordingApprover):
            def confirm_video_batch(inner, request):
                token = super().confirm_video_batch(request)
                resolver.quote["total_credit_ceiling"] = 1
                return token

        allowances = make_allowances(self.root, self.key_path, quote_resolver=resolver)
        with self.assertRaises(BatchScopeError):
            allowances.activate(self.quote, ReplacingApprover())
        self.assertEqual(list((self.root / "allowances").glob("*.json")), [])

    def test_same_shot_attempt_tuple_is_allowed_in_distinct_allowances_with_distinct_fingerprints(self) -> None:
        first_id = self.activate()
        self.reserve_first(first_id, self.quote)
        second_quote = copy.deepcopy(self.quote)
        second_quote["project_id"] = "vp_" + "7" * 24
        second_quote["creative_mode"] = "original_redesign"
        second_quote.pop("rights_receipt_id")
        second_quote.pop("rights_receipt_fingerprint")
        for index, attempt in enumerate(second_quote["items"][0]["attempts"], start=1):
            attempt["request"]["prompt"] += f" tuple-{index}"
            attempt["request_fingerprint"] = build_video_request_fingerprint(attempt["request"])
        second_quote["items"][0]["request_fingerprints"] = [
            attempt["request_fingerprint"] for attempt in second_quote["items"][0]["attempts"]
        ]
        second_quote["quote_fingerprint"] = canonical_fingerprint(
            {key: value for key, value in second_quote.items() if key != "quote_fingerprint"}
        )
        second_id = self.allowances.activate(second_quote, self.approver)
        second = self.reserve_first(second_id, second_quote)
        self.assertEqual(second["allowance_id"], second_id)
        self.assertEqual(second["shot_id"], "S01")
        self.assertEqual(second["attempt"], 1)

    def test_duplicate_shot_attempt_tuple_inside_one_quote_is_rejected(self) -> None:
        corrupt = copy.deepcopy(self.quote)
        corrupt["items"][0]["attempts"][1]["attempt_number"] = 1
        corrupt["quote_fingerprint"] = canonical_fingerprint(
            {key: value for key, value in corrupt.items() if key != "quote_fingerprint"}
        )
        with self.assertRaises(BatchScopeError):
            self.allowances.activate(corrupt, self.approver)

    def test_symlink_root_and_replaced_allowances_directory_fail_closed(self) -> None:
        target = Path(self.temp.name) / "symlink-target"
        target.mkdir(mode=0o700)
        link = Path(self.temp.name) / "symlink-root"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(BatchScopeError):
            make_allowances(link, Path(self.temp.name) / "symlink.key")

        allowance_id = self.activate()
        directory = self.root / "allowances"
        moved = self.root / "allowances-old"
        directory.rename(moved)
        directory.mkdir(mode=0o700)
        with self.assertRaises(BatchScopeError):
            self.allowances.get(allowance_id)

    def test_same_instance_threads_serialize_even_when_flock_is_process_local(self) -> None:
        allowance_id = self.activate()
        attempt = self.quote["items"][0]["attempts"][0]
        start = threading.Barrier(3)
        scan_gate = threading.Barrier(2)
        results: list[str] = []
        original_scan = self.allowances._all_allowances

        def synchronized_scan(*args, **kwargs):
            value = original_scan(*args, **kwargs)
            try:
                scan_gate.wait(timeout=0.1)
            except threading.BrokenBarrierError:
                pass
            return value

        def worker():
            start.wait()
            try:
                self.allowances.reserve(
                    allowance_id, shot_id="S01", attempt=1,
                    request_fingerprint=attempt["request_fingerprint"],
                )
                results.append("reserved")
            except ReservationConsumedError:
                results.append("rejected")

        with patch.object(self.allowances, "_all_allowances", side_effect=synchronized_scan), patch(
            "scripts.video_batch_allowance.fcntl.flock", return_value=None
        ):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(results.count("reserved"), 1)
        self.assertEqual(results.count("rejected"), 1)

    def test_short_os_write_is_retried_until_complete(self) -> None:
        allowance_id = self.activate()
        attempt = self.quote["items"][0]["attempts"][0]
        real_write = os.write

        def short_write(descriptor, data):
            return real_write(descriptor, data[: max(1, len(data) // 3)])

        with patch("scripts.video_batch_allowance.os.write", side_effect=short_write):
            self.allowances.reserve(
                allowance_id, shot_id="S01", attempt=1,
                request_fingerprint=attempt["request_fingerprint"],
            )
        restarted = make_allowances(self.root, self.key_path)
        self.assertEqual(restarted.get(allowance_id)["consumed_credits"], 7)

    def test_unsafe_read_failures_do_not_leak_file_descriptors(self) -> None:
        allowance_id = self.activate()
        allowance_path = self.root / "allowances" / f"{allowance_id}.json"
        allowance_path.chmod(0o644)
        before = len(os.listdir("/dev/fd"))
        for _ in range(25):
            with self.assertRaises(BatchScopeError):
                self.allowances.get(allowance_id)
        self.assertLessEqual(len(os.listdir("/dev/fd")), before + 1)

        resolver = LocalQuoteResolver(Path(self.temp.name) / "resolver")
        identity = resolver.persist(self.quote)
        quote_path = resolver._root / f"{identity['fingerprint']}.json"
        quote_path.chmod(0o644)
        before = len(os.listdir("/dev/fd"))
        for _ in range(25):
            with self.assertRaises(BatchScopeError):
                resolver.resolve(identity)
        self.assertLessEqual(len(os.listdir("/dev/fd")), before + 1)
        resolver.close()

    def test_valid_hmac_cannot_hide_impossible_reservation_history(self) -> None:
        allowance_id = self.activate()
        reservation = self.reserve_first(allowance_id, self.quote)
        path = self.root / "allowances" / f"{allowance_id}.json"
        stored = json.loads(path.read_text())
        stored["reservations"][0]["history"].append({
            "state": "committed", "at": "2026-09-14T00:00:01Z", "submit_id": "submit_hidden"
        })
        key = FileSealKeyStore(self.key_path).load(allow_create=False)
        self.allowances._seal(stored, key)
        path.write_text(json.dumps(stored))
        path.chmod(0o600)
        with self.assertRaises(BatchScopeError):
            self.allowances.get(allowance_id)

    def test_preexisting_root_symlink_and_public_directories_are_rejected_without_chmod(self) -> None:
        root = Path(self.temp.name) / "unsafe-root"
        target = Path(self.temp.name) / "target"
        target.mkdir(mode=0o700)
        root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(BatchScopeError):
            VideoBatchAllowance(root, seal_key_store=object(), quote_resolver=object())

        root.unlink()
        root.mkdir(mode=0o755)
        with self.assertRaises(BatchScopeError):
            VideoBatchAllowance(root, seal_key_store=object(), quote_resolver=object())
        self.assertEqual(root.stat().st_mode & 0o777, 0o755)

        root.chmod(0o700)
        child = root / "allowances"
        child.mkdir(mode=0o755)
        with self.assertRaises(BatchScopeError):
            VideoBatchAllowance(root, seal_key_store=object(), quote_resolver=object())
        self.assertEqual(child.stat().st_mode & 0o777, 0o755)

    def test_foreign_owner_child_simulation_fails_without_chmod(self) -> None:
        root = Path(self.temp.name) / "foreign-child"
        root.mkdir(mode=0o700)
        (root / "allowances").mkdir(mode=0o700)
        (root / "activations").mkdir(mode=0o700)
        real_fstat = os.fstat
        fstat_calls = 0

        def foreign_allowances(descriptor):
            nonlocal fstat_calls
            fstat_calls += 1
            metadata = real_fstat(descriptor)
            if fstat_calls == 2:
                return type("ForeignStat", (), {
                    "st_mode": metadata.st_mode, "st_uid": os.getuid() + 1,
                    "st_dev": metadata.st_dev, "st_ino": metadata.st_ino,
                })()
            return metadata

        with patch("scripts.video_batch_allowance.os.fstat", side_effect=foreign_allowances):
            with self.assertRaises(BatchScopeError):
                VideoBatchAllowance(root, seal_key_store=object(), quote_resolver=object())
        self.assertEqual((root / "allowances").stat().st_mode & 0o777, 0o700)

    def test_root_swap_between_mkdir_and_open_is_rejected(self) -> None:
        root = Path(self.temp.name) / "swap-root"
        real_open = os.open
        swapped = False

        def swapping_open(path, *args, **kwargs):
            nonlocal swapped
            if path == root.name and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                root.rename(Path(self.temp.name) / "original-root")
                root.mkdir(mode=0o700)
            return real_open(path, *args, **kwargs)

        with patch("scripts.video_batch_allowance.os.open", side_effect=swapping_open):
            with self.assertRaises(BatchScopeError):
                VideoBatchAllowance(root, seal_key_store=object(), quote_resolver=object())

    def test_parent_replacement_after_initialization_blocks_operation(self) -> None:
        allowance_id = self.activate()
        parent = self.root.parent
        moved = parent.with_name(parent.name + "-moved")
        parent.rename(moved)
        parent.mkdir(mode=0o700)
        with self.assertRaises(BatchScopeError):
            self.allowances.get(allowance_id)

    def test_partial_constructor_failures_close_all_opened_descriptors(self) -> None:
        for fail_call in range(1, 5):
            root = Path(self.temp.name) / f"partial-{fail_call}"
            before = len(os.listdir("/dev/fd"))
            real_open = os.open
            calls = 0

            def failing_open(path, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == fail_call:
                    raise OSError("injected constructor failure")
                return real_open(path, *args, **kwargs)

            with patch("scripts.video_batch_allowance.os.open", side_effect=failing_open):
                with self.assertRaises(BatchScopeError):
                    VideoBatchAllowance(root, seal_key_store=object(), quote_resolver=object())
            self.assertLessEqual(len(os.listdir("/dev/fd")), before + 1)

        for fail_call in (1, 2):
            root = Path(self.temp.name) / f"resolver-partial-{fail_call}"
            before = len(os.listdir("/dev/fd"))
            real_open = os.open
            calls = 0

            def failing_resolver_open(path, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == fail_call:
                    raise OSError("injected resolver failure")
                return real_open(path, *args, **kwargs)

            with patch("scripts.video_batch_allowance.os.open", side_effect=failing_resolver_open):
                with self.assertRaises(BatchScopeError):
                    LocalQuoteResolver(root)
            self.assertLessEqual(len(os.listdir("/dev/fd")), before + 1)

    def test_concurrent_normal_initialization_is_idempotent(self) -> None:
        root = Path(self.temp.name) / "concurrent-init"
        start = threading.Barrier(9)
        failures = []

        def initialize():
            start.wait()
            try:
                instance = VideoBatchAllowance(root, seal_key_store=object(), quote_resolver=object())
                instance.close()
            except Exception as exc:
                failures.append(exc)

        threads = [threading.Thread(target=initialize) for _ in range(8)]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(failures, [])
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_close_is_idempotent_and_future_operations_fail_with_typed_error(self) -> None:
        allowance_id = self.activate()
        self.allowances.close()
        self.allowances.close()
        with self.assertRaises(BatchScopeError):
            self.allowances.get(allowance_id)

    def test_original_and_replication_design_are_reloaded_after_dialog(self) -> None:
        for mode in ("authorized_replication", "original_redesign"):
            with self.subTest(mode=mode):
                quote = copy.deepcopy(self.quote)
                quote["creative_mode"] = mode
                if mode == "original_redesign":
                    quote.pop("rights_receipt_id")
                    quote.pop("rights_receipt_fingerprint")
                quote["quote_fingerprint"] = canonical_fingerprint(
                    {key: value for key, value in quote.items() if key != "quote_fingerprint"}
                )

                class MutableDesignStore(FakeProjectStore):
                    removed = False

                    def read_version(inner, *args, **kwargs):
                        if inner.removed:
                            raise FileNotFoundError("design removed")
                        return super().read_version(*args, **kwargs)

                store = MutableDesignStore()

                class RemovingApprover(RecordingApprover):
                    def confirm_video_batch(inner, request):
                        token = super().confirm_video_batch(request)
                        store.removed = True
                        return token

                target_root = Path(self.temp.name) / f"design-{mode}"
                allowances = VideoBatchAllowance(
                    target_root,
                    seal_key_store=FileSealKeyStore(Path(self.temp.name) / f"{mode}.key"),
                    project_store=store, rights_service=FakeRightsService(),
                )
                with self.assertRaises(BatchScopeError):
                    allowances.activate(quote, RemovingApprover())
                self.assertEqual(list((target_root / "allowances").glob("*.json")), [])

    def test_quote_freshness_is_rechecked_after_native_dialog(self) -> None:
        current = [datetime(2026, 9, 14, 1, tzinfo=timezone.utc)]

        class AdvancingApprover(RecordingApprover):
            def confirm_video_batch(inner, request):
                token = super().confirm_video_batch(request)
                current[0] = datetime(2026, 9, 16, 1, tzinfo=timezone.utc)
                return token

        allowances = VideoBatchAllowance(
            Path(self.temp.name) / "freshness",
            seal_key_store=FileSealKeyStore(Path(self.temp.name) / "freshness.key"),
            project_store=FakeProjectStore(), rights_service=FakeRightsService(),
            now=lambda: current[0],
        )
        with self.assertRaises(BatchScopeError):
            allowances.activate(self.quote, AdvancingApprover())

    def test_cost_basis_timestamp_requires_strict_rfc3339_in_quote_and_activation(self) -> None:
        for value in ("2026-09-14", "2026-09-14 00:00:00+00:00", "2026-09-14T00:00:00", "not-a-time"):
            with self.subTest(value=value):
                quote = copy.deepcopy(self.quote)
                quote["cost_basis"]["recorded_at"] = value
                quote["quote_fingerprint"] = canonical_fingerprint(
                    {key: item for key, item in quote.items() if key != "quote_fingerprint"}
                )
                with self.assertRaises(ContractValidationError):
                    validate_batch_quote(quote)
                with self.assertRaises(BatchScopeError):
                    self.allowances.activate(quote, self.approver)

    def test_cost_basis_timestamp_accepts_z_and_numeric_offsets_with_freshness(self) -> None:
        cases = (
            ("2026-09-14T00:00:00Z", datetime(2026, 9, 14, 1, tzinfo=timezone.utc)),
            ("2026-09-14T08:00:00+08:00", datetime(2026, 9, 14, 1, tzinfo=timezone.utc)),
            ("2026-09-13T19:00:00-05:00", datetime(2026, 9, 14, 1, tzinfo=timezone.utc)),
        )
        for index, (value, current) in enumerate(cases):
            with self.subTest(value=value):
                quote = copy.deepcopy(self.quote)
                quote["project_id"] = "vp_" + str(index + 6) * 24
                quote["creative_mode"] = "original_redesign"
                quote.pop("rights_receipt_id")
                quote.pop("rights_receipt_fingerprint")
                quote["cost_basis"]["recorded_at"] = value
                quote["quoted_at"] = value
                quote["quote_fingerprint"] = canonical_fingerprint(
                    {key: item for key, item in quote.items() if key != "quote_fingerprint"}
                )
                validate_batch_quote(quote)
                target = VideoBatchAllowance(
                    Path(self.temp.name) / f"rfc3339-{index}",
                    seal_key_store=FileSealKeyStore(Path(self.temp.name) / f"rfc3339-{index}.key"),
                    project_store=FakeProjectStore(), rights_service=FakeRightsService(),
                    now=lambda current=current: current,
                )
                self.assertTrue(target.activate(quote, self.approver).startswith("ba_"))

    def test_sealed_history_rejects_orphans_duplicates_truncation_and_wrong_exhaustion(self) -> None:
        mutations = (
            lambda state: state["history"].append({"state": "reserved", "at": state["activated_at"], "reservation_id": "br_" + "f" * 32}),
            lambda state: state["history"].append(copy.deepcopy(state["history"][-1])),
            lambda state: state["history"].pop(),
            lambda state: state.update({"state": "exhausted"}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                root = Path(self.temp.name) / f"history-{index}"
                key_path = Path(self.temp.name) / f"history-{index}.key"
                allowances = make_allowances(root, key_path)
                allowance_id = allowances.activate(self.quote, self.approver)
                attempt = self.quote["items"][0]["attempts"][0]
                allowances.reserve(
                    allowance_id, shot_id="S01", attempt=1,
                    request_fingerprint=attempt["request_fingerprint"],
                )
                path = root / "allowances" / f"{allowance_id}.json"
                state = json.loads(path.read_text())
                mutate(state)
                allowances._seal(state, FileSealKeyStore(key_path).load(allow_create=False))
                path.write_text(json.dumps(state))
                path.chmod(0o600)
                with self.assertRaises(BatchScopeError):
                    allowances.get(allowance_id)


if __name__ == "__main__":
    unittest.main()
