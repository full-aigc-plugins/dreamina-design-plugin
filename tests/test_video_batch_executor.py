from __future__ import annotations

import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.dreamina_adapter import DreaminaResult
from scripts.video_batch_executor import VideoBatchExecutor
from scripts.video_service import build_video_request_fingerprint


PROJECT_ID = "vp_" + "1" * 24


def request(prompt: str) -> dict:
    return {"mode": "text2video", "model": "seedance-test", "prompt": prompt,
            "video_resolution": "720p", "duration_seconds": 4}


def quote() -> dict:
    one, retry, two = request("shot one"), request("shot one repaired"), request("shot two")
    return {"project_id": PROJECT_ID, "quote_version": "v001", "items": [
        {"shot_id": "S01", "attempts": [
          {"attempt_number": 2, "request": retry, "request_fingerprint": build_video_request_fingerprint(retry)},
          {"attempt_number": 1, "request": one, "request_fingerprint": build_video_request_fingerprint(one)}]},
        {"shot_id": "S02", "attempts": [{"attempt_number": 1, "request": two,
          "request_fingerprint": build_video_request_fingerprint(two)}]},
    ]}


class Store:
    def __init__(self, root: Path): self.root, self.document = root, quote()
    def read_version(self, project_id, family, version, schema):
        assert (project_id, family, version, schema) == (PROJECT_ID, "video_batch_quote", "v001", "video_batch_quote.schema.json")
        return copy.deepcopy(self.document)
    def project_root(self, project_id):
        path = self.root / project_id; path.mkdir(parents=True, exist_ok=True); return path


class Allowance:
    def __init__(self): self.reservations = []; self.ambiguous = []; self.commits = []; self.fail_commit = False
    def get(self, allowance_id):
        return {"allowance_id": allowance_id, "project_id": PROJECT_ID, "quote_version": "v001",
                "state": "active", "reservations": copy.deepcopy(self.reservations)}
    def reserve(self, allowance_id, *, shot_id, attempt, request_fingerprint):
        reservation = {"reservation_id": f"br_{len(self.reservations)+1:032x}", "allowance_id": allowance_id,
                       "shot_id": shot_id, "attempt": attempt, "request_fingerprint": request_fingerprint, "state": "reserved"}
        self.reservations.append(reservation); return copy.deepcopy(reservation)
    def commit(self, reservation_id, submit_id):
        self.commits.append((reservation_id, submit_id))
        if self.fail_commit: raise RuntimeError("indeterminate commit")
        for item in self.reservations:
            if item["reservation_id"] == reservation_id: item.update(state="committed", submit_id=submit_id); return copy.deepcopy(item)
    def mark_ambiguous(self, reservation_id, error_code, *, submit_id=None):
        self.ambiguous.append((reservation_id, error_code, submit_id))
        for item in self.reservations:
            if item["reservation_id"] == reservation_id:
                item.update(state="ambiguous", error_code=error_code, required_action="query" if submit_id else "manual_review")
                if submit_id: item["submit_id"] = submit_id
                return copy.deepcopy(item)


class Adapter:
    def __init__(self): self.calls = []; self.raise_after_invoke = None; self.status = "querying"; self.status_by_submit = {}; self.download = False; self.corrupt_download = False; self.submissions = 0
    def run(self, args):
        self.calls.append(list(args))
        if args[0] == "query_result":
            if self.download and "--download_dir" in args:
                target = Path(args[args.index("--download_dir") + 1]) / "clip.mp4"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"not-media" if self.corrupt_download else b"\0\0\0\x18ftypisom" + b"x" * 20)
            status = self.status_by_submit.get(args[2], self.status)
            return DreaminaResult(0, {"gen_status": status}, None, args[2], "")
        if self.raise_after_invoke: raise self.raise_after_invoke
        self.submissions += 1
        return DreaminaResult(0, {"submit_id": f"submit_{self.submissions}"}, None, f"submit_{self.submissions}", "")


class TimeoutWithSubmitId(TimeoutError):
    def __init__(self, submit_id: str):
        super().__init__("timeout after provider accepted request")
        self.submit_id = submit_id


class VideoBatchExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)); self.allowance = Allowance(); self.adapter = Adapter()
        self.executor = VideoBatchExecutor(project_store=self.store, allowance=self.allowance,
            allowance_id="ba_" + "2" * 32, video_service=None, adapter=self.adapter)

    def test_run_next_submits_only_next_request_in_shot_and_attempt_order(self):
        result = self.executor.run_next(PROJECT_ID, "v001", 1)
        self.assertEqual(result["new_submissions"], 1)
        self.assertEqual((result["state"], result["required_action"]), ("generating", "query"))
        self.assertEqual(self.adapter.calls, [["text2video", "--model_version", "seedance-test", "--prompt", "shot one", "--video_resolution", "720p", "--duration", "4"]])

    def test_max_new_submissions_is_bounded(self):
        for value in (0, 5, True):
            with self.subTest(value=value), self.assertRaises(ValueError): self.executor.run_next(PROJECT_ID, "v001", value)

    def test_ambiguous_result_enters_manual_review_without_second_call(self):
        self.adapter.raise_after_invoke = TimeoutError("unknown remote outcome")
        result = self.executor.run_next(PROJECT_ID, "v001", 1)
        self.assertEqual((result["state"], result["required_action"]), ("manual_review", "manual_review")); self.assertEqual(len(self.adapter.calls), 1)
        self.executor.resume(PROJECT_ID, "v001"); self.assertEqual(len(self.adapter.calls), 1)

    def test_resume_queries_known_submit_id_before_new_submission_and_survives_restart(self):
        self.executor.run_next(PROJECT_ID, "v001", 1); self.adapter.calls.clear()
        restarted = VideoBatchExecutor(project_store=self.store, allowance=self.allowance,
            allowance_id="ba_" + "2" * 32, video_service=None, adapter=self.adapter)
        restarted.resume(PROJECT_ID, "v001")
        self.assertEqual(self.adapter.calls[0][:3], ["query_result", "--submit_id", "submit_1"])
        self.assertTrue(all(call[0] == "query_result" for call in self.adapter.calls))

    def test_run_next_queries_known_submit_before_any_new_submission_after_restart(self):
        self.executor.run_next(PROJECT_ID, "v001", 1); self.adapter.calls.clear()
        restarted = VideoBatchExecutor(project_store=self.store, allowance=self.allowance,
            allowance_id="ba_" + "2" * 32, video_service=None, adapter=self.adapter)
        restarted.run_next(PROJECT_ID, "v001", 1)
        self.assertEqual(self.adapter.calls[0][:3], ["query_result", "--submit_id", "submit_1"])

    def test_run_next_reconciles_every_known_submit_id_before_new_submission(self):
        self.executor.run_next(PROJECT_ID, "v001", 1)
        prior_calls = len(self.adapter.calls)
        result = self.executor.run_next(PROJECT_ID, "v001", 1)
        self.assertEqual(result["new_submissions"], 1)
        new_calls = self.adapter.calls[prior_calls:]
        self.assertEqual(new_calls[0][:3], ["query_result", "--submit_id", "submit_1"])
        self.assertEqual(new_calls[1][0], "text2video")

    def test_timeout_with_known_submit_id_is_queryable_after_restart(self):
        self.adapter.raise_after_invoke = TimeoutWithSubmitId("submit_known")
        first = self.executor.run_next(PROJECT_ID, "v001", 1)
        self.assertEqual(first["state"], "manual_review")
        self.assertEqual(first["required_action"], "manual_review")
        self.assertEqual(first["tasks"][0]["submit_id"], "submit_known")
        self.assertEqual(self.allowance.ambiguous[0][2], "submit_known")
        receipt = self.executor._ledger.get(submit_id="submit_known")
        self.assertEqual(receipt["allowance_id"], "ba_" + "2" * 32)
        self.assertEqual(receipt["reservation_id"], "br_" + "0" * 31 + "1")

        self.adapter.raise_after_invoke = None
        self.adapter.calls.clear()
        restarted = VideoBatchExecutor(project_store=self.store, allowance=self.allowance,
            allowance_id="ba_" + "2" * 32, video_service=None, adapter=self.adapter)
        restarted.resume(PROJECT_ID, "v001")
        self.assertEqual(self.adapter.calls[0][:3], ["query_result", "--submit_id", "submit_known"])

    def test_failed_and_unknown_tasks_are_explicit_and_never_resubmit(self):
        for status, expected in (("failed", "failed"), ("mystery", "manual_review")):
            with self.subTest(status=status):
                self.setUp()
                self.executor.run_next(PROJECT_ID, "v001", 1)
                self.adapter.status = status; self.adapter.calls.clear()
                result = self.executor.reconcile(PROJECT_ID, "v001")
                self.assertEqual(result["state"], expected); self.assertTrue(all(c[0] == "query_result" for c in self.adapter.calls))
                self.assertEqual(result["required_action"], "report_failure" if expected == "failed" else "manual_review")

    def test_run_next_does_not_submit_new_work_after_terminal_failure(self):
        self.executor.run_next(PROJECT_ID, "v001", 1)
        self.adapter.status = "failed"
        self.adapter.calls.clear()
        result = self.executor.run_next(PROJECT_ID, "v001", 1)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["required_action"], "report_failure")
        self.assertEqual(result["new_submissions"], 0)
        self.assertTrue(all(call[0] == "query_result" for call in self.adapter.calls))

    def test_success_download_is_verified_and_missing_artifact_is_explicit(self):
        self.executor.run_next(PROJECT_ID, "v001", 1); self.adapter.status = "success"
        result = self.executor.reconcile(PROJECT_ID, "v001")
        self.assertEqual(result["state"], "missing_artifact")
        self.adapter.download = True
        result = self.executor.reconcile(PROJECT_ID, "v001")
        self.assertEqual(result["state"], "awaiting_evaluation")
        artifact = result["tasks"][0]["artifacts"][0]
        self.assertEqual(artifact["provenance"], "externally-queried"); self.assertEqual(len(artifact["sha256"]), 64)
        self.adapter.calls.clear()
        blocked = self.executor.run_next(PROJECT_ID, "v001", 1)
        self.assertEqual(blocked["new_submissions"], 0)
        self.assertEqual((blocked["state"], blocked["required_action"]), ("awaiting_evaluation", "evaluate"))
        self.executor._record_evaluation_decision(PROJECT_ID, "v001", "S01", 1, "retry")
        retried = self.executor.run_next(PROJECT_ID, "v001", 1)
        self.assertEqual(retried["new_submissions"], 1)
        self.assertEqual(self.adapter.calls[-1][0], "text2video")

    def test_corrupt_download_is_persisted_as_manual_review(self):
        self.executor.run_next(PROJECT_ID, "v001", 1)
        self.adapter.status = "success"
        self.adapter.download = True
        self.adapter.corrupt_download = True
        result = self.executor.reconcile(PROJECT_ID, "v001")
        self.assertEqual(result["state"], "manual_review")
        self.assertEqual(result["required_action"], "manual_review")
        self.assertEqual(result["tasks"][0]["state"], "manual_review")
        self.assertEqual(result["tasks"][0]["error_code"], "INVALID_DOWNLOADED_ARTIFACT")

    def test_commit_indeterminate_preserves_identity_and_restart_queries_submit_id(self):
        self.allowance.fail_commit = True
        result = self.executor.run_next(PROJECT_ID, "v001", 1)
        task = result["tasks"][0]
        self.assertEqual(result["state"], "manual_review")
        self.assertEqual(result["required_action"], "manual_review")
        self.assertEqual(task["submit_id"], "submit_1")
        self.assertTrue(task["reservation_id"].startswith("br_"))
        self.assertEqual(task["request_fingerprint"], build_video_request_fingerprint(request("shot one")))
        self.allowance.fail_commit = False; self.adapter.calls.clear()
        restarted = VideoBatchExecutor(project_store=self.store, allowance=self.allowance,
            allowance_id="ba_" + "2" * 32, video_service=None, adapter=self.adapter)
        restarted.reconcile(PROJECT_ID, "v001")
        self.assertEqual(self.adapter.calls[0][:3], ["query_result", "--submit_id", "submit_1"])

    def test_retry_submission_consumes_predecessor_and_restart_cannot_duplicate_it(self):
        self.executor.run_next(PROJECT_ID, "v001", 1)
        self.adapter.status = "success"; self.adapter.download = True
        self.executor.reconcile(PROJECT_ID, "v001")
        self.executor._record_evaluation_decision(PROJECT_ID, "v001", "S01", 1, "retry")
        submitted = self.executor.run_next(PROJECT_ID, "v001", 1)
        predecessor = next(task for task in submitted["tasks"] if task["attempt"] == 1)
        self.assertEqual(predecessor["state"], "retry_superseded")
        calls_before = self.adapter.submissions
        restarted = VideoBatchExecutor(project_store=self.store, allowance=self.allowance,
            allowance_id="ba_" + "2" * 32, video_service=None, adapter=self.adapter)
        resumed = restarted.run_next(PROJECT_ID, "v001", 1)
        self.assertEqual(self.adapter.submissions, calls_before)
        self.assertEqual((resumed["state"], resumed["required_action"]), ("awaiting_evaluation", "evaluate"))

    def test_aggregate_state_precedence_table(self):
        cases = [
            (["failed", "manual_review", "queued"], ("manual_review", "manual_review")),
            (["awaiting_evaluation", "failed", "queued"], ("failed", "report_failure")),
            (["queued", "awaiting_evaluation"], ("awaiting_evaluation", "evaluate")),
            (["accepted", "queued"], ("generating", "query")),
            (["accepted", "evaluation_retryable"], ("evaluation_retryable", "run_next")),
            (["accepted", "accepted"], ("completed", "none")),
        ]
        for task_states, expected in cases:
            with self.subTest(task_states=task_states):
                tasks = [{"state": value} for value in task_states]
                self.assertEqual(VideoBatchExecutor.aggregate_state(tasks), expected)
                self.assertEqual(VideoBatchExecutor.aggregate_state(list(reversed(tasks))), expected)

    def test_two_known_tasks_retain_dominant_state_and_block_new_submission(self):
        for dominant_status, expected in (("mystery", "manual_review"), ("failed", "failed")):
            for reverse in (False, True):
                with self.subTest(dominant_status=dominant_status, reverse=reverse):
                    self.setUp(); self.executor.run_next(PROJECT_ID, "v001", 2)
                    state = self.executor._load(PROJECT_ID, "v001")
                    if reverse:
                        state["tasks"].reverse(); self.executor._save(state)
                    self.adapter.status_by_submit = {"submit_1": dominant_status, "submit_2": "querying"}
                    self.adapter.calls.clear()
                    result = self.executor.run_next(PROJECT_ID, "v001", 1)
                    self.assertEqual(result["state"], expected)
                    self.assertEqual(result["new_submissions"], 0)
                    self.assertEqual([call[2] for call in self.adapter.calls if call[0] == "query_result"],
                                     ["submit_1", "submit_2"])

    def test_parallel_executors_have_one_winner_for_single_remaining_request(self):
        self.store.document["items"] = self.store.document["items"][:1]
        self.store.document["items"][0]["attempts"] = self.store.document["items"][0]["attempts"][1:]
        second = VideoBatchExecutor(project_store=self.store, allowance=self.allowance,
            allowance_id="ba_" + "2" * 32, video_service=None, adapter=self.adapter)
        gate = threading.Barrier(2)
        def run(executor):
            gate.wait()
            return executor.run_next(PROJECT_ID, "v001", 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (self.executor, second)))
        self.assertEqual(self.adapter.submissions, 1)
        self.assertEqual(sum(result["new_submissions"] for result in results), 1)

    def test_restart_recovers_known_submit_identity_from_allowance(self):
        planned = self.store.document["items"][0]["attempts"][1]
        self.allowance.reservations.append({"reservation_id": "br_" + "1" * 32,
            "shot_id": "S01", "attempt": 1, "request_fingerprint": planned["request_fingerprint"],
            "state": "ambiguous", "submit_id": "submit_recovered"})
        self.executor._save({"project_id": PROJECT_ID, "batch_version": "v001",
            "allowance_id": "ba_" + "2" * 32, "state": "generating", "tasks": [{
                "shot_index": 0, "shot_id": "S01", "attempt": 1,
                "request_fingerprint": planned["request_fingerprint"], "state": "submitting", "artifacts": []}]})
        result = self.executor.resume(PROJECT_ID, "v001")
        self.assertEqual(result["tasks"][0]["submit_id"], "submit_recovered")

    def test_evaluation_mutator_is_not_public_task9_api(self):
        self.assertFalse(hasattr(self.executor, "record_evaluation_decision"))


if __name__ == "__main__": unittest.main()
