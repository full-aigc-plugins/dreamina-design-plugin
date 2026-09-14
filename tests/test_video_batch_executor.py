from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from scripts.dreamina_adapter import DreaminaResult
from scripts.video_batch_executor import VideoBatchExecutor
from scripts.video_service import build_video_request_fingerprint


PROJECT_ID = "vp_" + "1" * 24


def request(prompt: str) -> dict:
    return {"mode": "text2video", "model": "seedance-test", "prompt": prompt,
            "video_resolution": "720p", "duration_seconds": 4}


def quote() -> dict:
    one, retry, two = request("shot one"), request("shot one repaired"), request("shot two")
    return {"project_id": PROJECT_ID, "quote_version": "v001", "design_version": "v001",
            "design_fingerprint": "b" * 64, "quote_fingerprint": "c" * 64, "items": [
        {"shot_id": "S01", "attempts": [
          {"attempt_number": 2, "repair_directive": "identity_consistency", "request": retry, "request_fingerprint": build_video_request_fingerprint(retry)},
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
                "quote_fingerprint": "c" * 64, "design_version": "v001", "design_fingerprint": "b" * 64,
                "state": "active", "requests": [{"shot_id": "S01", "attempt": 2,
                "request_fingerprint": build_video_request_fingerprint(request("shot one repaired"))}],
                "reservations": copy.deepcopy(self.reservations)}
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
        super().__init__("timeout token=SECRET_TOKEN https://signed.example/?key=SECRET_KEY")
        self.submit_id = submit_id
        self.result_bytes = b'{"submit_id":"submit_known","token":"SECRET_TOKEN"}'
        self.invocation_started = True
        self.outcome_ambiguous = True


class VideoBatchExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)); self.allowance = Allowance(); self.adapter = Adapter()
        self.executor = VideoBatchExecutor(project_store=self.store, allowance=self.allowance,
            allowance_id="ba_" + "2" * 32, video_service=None, adapter=self.adapter)

    def record_retry(self):
        task = self.executor._load(PROJECT_ID, "v001")["tasks"][0]
        binding = {"project_id": PROJECT_ID, "batch_version": "v001", "design_version": "v001",
                   "shot_id": "S01", "attempt": 1, "artifact_sha256": task["artifacts"][0]["sha256"],
                   "submit_id": task["submit_id"], "evaluation_id": "eval_1", "decision": "retry"}
        return self.executor._record_evaluation_decision(
            PROJECT_ID, "v001", "S01", 1, "retry", artifact_sha256=binding["artifact_sha256"],
            submit_id=binding["submit_id"], evaluation_id="eval_1",
            evaluation_fingerprint=self.executor._evaluation_fingerprint(binding), design_version="v001")

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
        self.assertEqual(first["tasks"][0]["evidence_length"], len(self.adapter.raise_after_invoke.result_bytes))
        self.assertEqual(len(first["tasks"][0]["evidence_sha256"]), 64)
        persisted = "\n".join(path.read_text(encoding="utf-8") for path in self.executor._root.rglob("*.json"))
        self.assertNotIn("SECRET_TOKEN", persisted)
        self.assertNotIn("SECRET_KEY", persisted)
        self.assertNotIn("signed.example", persisted)
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

    def test_postinvoke_evidence_is_digest_only_redacted_and_bounded(self):
        secret_error_type = type("SECRET_TOKEN_" + "X" * 5000, (TimeoutError,), {})
        failure = secret_error_type("cookie=SECRET_KEY https://signed.example/?token=SECRET_TOKEN")
        failure.submit_id = "submit_secret_safe"
        failure.result_bytes = json.dumps({
            "result": {"authorization": "Bearer SECRET_TOKEN", "nested": [{"api_key": "SECRET_KEY"}]},
            "signed_url": "https://signed.example/?token=SECRET_TOKEN",
        }).encode() + b"x" * (2 * 1024 * 1024)
        self.adapter.raise_after_invoke = failure

        result = self.executor.run_next(PROJECT_ID, "v001", 1)
        task = result["tasks"][0]
        self.assertEqual(task["submit_id"], "submit_secret_safe")
        self.assertEqual(task["evidence_length"], len(failure.result_bytes))
        self.assertEqual(len(task["evidence_sha256"]), 64)
        self.assertNotIn("result_bytes", task)
        self.assertNotIn("result_bytes_hex", task)
        self.assertNotIn("exception_type", task)
        self.assertNotIn("classification", task)
        serialized_result = json.dumps(result, sort_keys=True)
        persisted = "\n".join(path.read_text(encoding="utf-8") for path in self.executor._root.rglob("*.json"))
        for forbidden in ("SECRET_TOKEN", "SECRET_KEY", "signed.example", "authorization", "api_key", "signed_url"):
            self.assertNotIn(forbidden, serialized_result)
            self.assertNotIn(forbidden, persisted)
        self.assertLess(len(task["error_code"].encode()), 128)

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
        self.assertEqual(Path(artifact["path"]).stat().st_mode & 0o777, 0o400)
        for parent in Path(artifact["path"]).parents:
            if parent == Path(self.tmp.name): break
            if parent.name in {"downloads", "S01", "attempt-1", "download-001"}:
                self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
        self.adapter.calls.clear()
        blocked = self.executor.run_next(PROJECT_ID, "v001", 1)
        self.assertEqual(blocked["new_submissions"], 0)
        self.assertEqual((blocked["state"], blocked["required_action"]), ("awaiting_evaluation", "evaluate"))
        self.record_retry()
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
        self.record_retry()
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
            (["retry_superseded", "accepted", "accepted"], ("completed", "none")),
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

    def test_private_evaluation_handoff_rejects_stale_artifact_digest(self):
        self.executor.run_next(PROJECT_ID, "v001", 1)
        self.adapter.status = "success"; self.adapter.download = True
        self.executor.reconcile(PROJECT_ID, "v001")
        with self.assertRaisesRegex(ValueError, "artifact digest"):
            self.executor._record_evaluation_decision(
                PROJECT_ID, "v001", "S01", 1, "retry", artifact_sha256="f" * 64,
                submit_id="submit_1", evaluation_id="eval_1",
                evaluation_fingerprint="a" * 64, design_version="v001")

    def test_explicit_retry_decision_must_bind_prequoted_fingerprint_before_submission(self):
        self.executor.run_next(PROJECT_ID, "v001", 1)
        self.adapter.status = "success"; self.adapter.download = True
        state = self.executor.reconcile(PROJECT_ID, "v001")
        task = state["tasks"][0]
        decision = {"action": "retry", "repair_directive": "identity_consistency",
                    "request_fingerprint": build_video_request_fingerprint(request("shot one repaired")),
                    "failed_gates": ["identity_continuity"],
                    "binding": {"project_id": PROJECT_ID, "batch_version": "v001", "shot_id": "S01",
                    "attempt": 1, "artifact_sha256": task["artifacts"][0]["sha256"],
                    "design_version": "v001", "design_fingerprint": "b" * 64,
                    "quote_fingerprint": "c" * 64, "allowance_id": "ba_" + "2" * 32}}
        persisted = self.executor.apply_evaluation_decision(decision)
        self.assertEqual(persisted["tasks"][0]["evaluation_decision"], decision)
        self.assertEqual(self.executor.run_next(PROJECT_ID, "v001", 1)["new_submissions"], 1)

    def test_existing_symlink_broad_or_foreign_download_root_fails_without_mutation(self):
        for kind in ("symlink", "broad", "foreign"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                store = Store(Path(tmp)); project = store.project_root(PROJECT_ID)
                execution = project / "video_batch_execution"; execution.mkdir(mode=0o700)
                downloads = execution / "downloads"
                if kind == "symlink":
                    outside = Path(tmp) / "outside"; outside.mkdir(mode=0o700)
                    downloads.symlink_to(outside, target_is_directory=True)
                else:
                    downloads.mkdir(mode=0o755 if kind == "broad" else 0o700)
                context = patch("scripts.video_batch_executor.os.getuid", return_value=os.getuid() + 1) if kind == "foreign" else patch("scripts.video_batch_executor.os.getuid", wraps=os.getuid)
                with context, self.assertRaises((OSError, ValueError)):
                    VideoBatchExecutor(project_store=store, allowance=Allowance(), allowance_id="ba_" + "2" * 32,
                                       video_service=None, adapter=Adapter())
                if kind == "broad": self.assertEqual(downloads.stat().st_mode & 0o777, 0o755)

    def test_download_hierarchy_replacement_fails_closed_at_every_level(self):
        for level in range(4):
            with self.subTest(level=level):
                self.setUp()
                self.executor.run_next(PROJECT_ID, "v001", 1)
                self.adapter.status = "success"

                original_run = self.adapter.run
                def swapping_run(args, *, selected=level):
                    result = original_run(args)
                    if "--download_dir" in args:
                        target = Path(args[args.index("--download_dir") + 1])
                        (target / "clip.mp4").write_bytes(b"\0\0\0\x18ftypisom" + b"x" * 20)
                        victim = (target, target.parent, target.parent.parent, target.parent.parent.parent)[selected]
                        moved = victim.with_name(victim.name + "-moved")
                        victim.rename(moved)
                        victim.mkdir(mode=0o700, parents=True)
                    return result
                self.adapter.run = swapping_run
                result = self.executor.reconcile(PROJECT_ID, "v001")
                self.assertEqual(result["tasks"][0]["state"], "manual_review")
                self.assertEqual(result["tasks"][0]["error_code"], "INVALID_DOWNLOADED_ARTIFACT")


if __name__ == "__main__": unittest.main()
