from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.visual_quality_loop import (
    InvalidVisualTransition,
    VisualLoopService,
    VisualLoopStore,
)


class RecordingGenerationPort:
    def __init__(self, artifacts: list[dict]) -> None:
        self.artifacts = list(artifacts)
        self.calls: list[dict] = []

    def generate(self, request: dict) -> dict:
        self.calls.append(dict(request))
        return self.artifacts.pop(0)


class RecordingJudgePort:
    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)
        self.calls: list[dict] = []

    def evaluate(self, evidence: dict) -> dict:
        self.calls.append(dict(evidence))
        score = self.scores.pop(0)
        return {
            "provider": "test-host",
            "model": "independent-judge",
            "evaluated_at": "2026-09-21T00:00:00Z",
            "rubric_version": "dreamina-visual-v1",
            "score": score,
            "gates": {
                "composition": {"score": min(3.0, score * 0.3), "evidence": "checked"},
                "lighting": {"score": min(3.0, score * 0.3), "evidence": "checked"},
                "materials": {"score": min(3.0, score * 0.3), "evidence": "checked"},
                "detail": {"score": min(1.0, score * 0.1), "evidence": "checked"},
            },
            "blocking_gaps": [] if score >= 8 else ["composition"],
        }


class RecordingReplanPort:
    def __init__(self, request: dict) -> None:
        self.request = request
        self.calls: list[dict] = []

    def replan(self, evidence: dict) -> dict:
        self.calls.append(evidence)
        return dict(self.request)


class RecordingPreviewPort:
    def __init__(self, artifact: dict) -> None:
        self.artifact = artifact
        self.calls: list[dict] = []

    def capture(self, request: dict) -> dict:
        self.calls.append(request)
        return dict(self.artifact)


class VisualQualityLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.approved = self.root / "approved"
        self.approved.mkdir()
        self.target = self.approved / "target.png"
        self.target.write_bytes(b"target-image")
        self.store = VisualLoopStore(self.root / "state")

    def artifact(self, name: str, payload: bytes) -> dict:
        path = self.approved / f"{name}.png"
        path.write_bytes(payload)
        return {
            "submit_id": f"submit-{name}",
            "path": str(path),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "mime_type": "image/png",
        }

    def create_loop(self) -> dict:
        loop = self.store.create_loop(media_kind="image", max_attempts=2)
        return self.store.lock_target(
            loop["loop_id"],
            source_path=self.target,
            approved_roots=[self.approved],
            mime_type="image/png",
            width=1024,
            height=1024,
            source={"kind": "user_supplied"},
        )

    def test_target_receipt_is_content_bound_and_cannot_be_replaced(self) -> None:
        loop = self.create_loop()
        receipt = loop["target"]
        self.assertEqual(receipt["sha256"], hashlib.sha256(b"target-image").hexdigest())
        self.assertEqual(len(receipt["receipt_fingerprint"]), 64)
        other = self.approved / "other.png"
        other.write_bytes(b"other")
        with self.assertRaises(InvalidVisualTransition):
            self.store.lock_target(
                loop["loop_id"], source_path=other, approved_roots=[self.approved],
                mime_type="image/png", width=1, height=1, source={"kind": "user_supplied"},
            )

    def test_first_round_auto_evaluates_but_never_auto_retries(self) -> None:
        loop = self.create_loop()
        generation = RecordingGenerationPort([self.artifact("one", b"one")])
        judge = RecordingJudgePort([5.0])
        service = VisualLoopService(self.store, generation_port=generation, judge_port=judge)
        result = service.run_first_round(loop["loop_id"], {"prompt": "make it"})
        self.assertEqual(len(generation.calls), 1)
        self.assertEqual(len(judge.calls), 1)
        self.assertEqual(result["state"], "awaiting_approval")
        self.assertEqual(result["rounds"][0]["next_action"], "request_retry_approval")

    def test_one_preapproved_retry_is_allowed_and_third_attempt_is_refused(self) -> None:
        loop = self.create_loop()
        generation = RecordingGenerationPort([
            self.artifact("one", b"one"), self.artifact("two", b"two")
        ])
        judge = RecordingJudgePort([5.0, 8.5])
        service = VisualLoopService(self.store, generation_port=generation, judge_port=judge)
        request = {"prompt": "make it"}
        first = service.run_first_round(loop["loop_id"], request)
        retry = {"prompt": "make it", "repair_directive": "composition"}
        fingerprint = service.request_fingerprint(retry)
        self.store.activate_retry_allowance(
            loop["loop_id"], request_fingerprint=fingerprint,
            credit_ceiling=2, approver="test-user",
        )
        second = service.run_retry(first["loop_id"], retry)
        self.assertEqual(second["state"], "completed")
        self.assertEqual(len(generation.calls), 2)
        with self.assertRaises(InvalidVisualTransition):
            service.run_retry(first["loop_id"], retry)

    def test_stop_blocks_new_submission_without_claiming_remote_cancel(self) -> None:
        loop = self.create_loop()
        stopped = self.store.stop(loop["loop_id"], reason="user_requested")
        self.assertEqual(stopped["state"], "stopped")
        self.assertFalse(stopped["remote_cancelled"])
        service = VisualLoopService(
            self.store,
            generation_port=RecordingGenerationPort([self.artifact("one", b"one")]),
            judge_port=RecordingJudgePort([9.0]),
        )
        with self.assertRaises(InvalidVisualTransition):
            service.run_first_round(loop["loop_id"], {"prompt": "forbidden"})

    def test_retry_cannot_exceed_approved_credit_ceiling(self) -> None:
        loop = self.create_loop()
        service = VisualLoopService(
            self.store,
            generation_port=RecordingGenerationPort([self.artifact("one", b"one")]),
            judge_port=RecordingJudgePort([5.0]),
        )
        service.run_first_round(loop["loop_id"], {"prompt": "make it", "credit_ceiling": 1})
        retry = {"prompt": "repair", "credit_ceiling": 3}
        self.store.activate_retry_allowance(
            loop["loop_id"],
            request_fingerprint=service.request_fingerprint(retry),
            credit_ceiling=2,
            approver="test-user",
        )
        with self.assertRaises(InvalidVisualTransition):
            service.run_retry(loop["loop_id"], retry)

    def test_opt_in_third_round_requires_replan_then_exact_approval(self) -> None:
        loop = self.store.create_loop(media_kind="image", max_attempts=3)
        loop = self.store.lock_target(
            loop["loop_id"], source_path=self.target, approved_roots=[self.approved],
            mime_type="image/png", width=1024, height=1024, source={"kind": "user_supplied"},
        )
        generation = RecordingGenerationPort([
            self.artifact("one", b"one"), self.artifact("two", b"two"), self.artifact("three", b"three")
        ])
        judge = RecordingJudgePort([4.0, 4.5, 9.0])
        service = VisualLoopService(self.store, generation_port=generation, judge_port=judge)
        first_request = {"prompt": "first", "credit_ceiling": 1}
        service.run_first_round(loop["loop_id"], first_request)
        second_request = {"prompt": "repair composition", "credit_ceiling": 2}
        self.store.activate_retry_allowance(
            loop["loop_id"], request_fingerprint=service.request_fingerprint(second_request),
            credit_ceiling=2, approver="test-user",
        )
        second = service.run_retry(loop["loop_id"], second_request)
        self.assertEqual(second["state"], "replan_required")

        third_request = {"prompt": "replanned exact candidate", "credit_ceiling": 2}
        planned = service.propose_retry(loop["loop_id"], RecordingReplanPort(third_request))
        proposal = planned["proposed_retry"]
        self.store.activate_retry_allowance(
            loop["loop_id"], request_fingerprint=proposal["request_fingerprint"],
            credit_ceiling=proposal["credit_ceiling"], approver="test-user",
        )
        final = service.run_retry(loop["loop_id"], proposal["request"])
        self.assertEqual(final["state"], "completed")
        self.assertEqual(len(generation.calls), 3)

    def test_dcc_preview_enters_same_receipt_and_judge_chain_without_paid_generation(self) -> None:
        loop = self.store.create_loop(media_kind="dcc_preview", max_attempts=1)
        loop = self.store.lock_target(
            loop["loop_id"], source_path=self.target, approved_roots=[self.approved],
            mime_type="image/png", width=1024, height=1024, source={"kind": "dcc_capture"},
        )
        generation = RecordingGenerationPort([])
        judge = RecordingJudgePort([9.0])
        preview = RecordingPreviewPort(self.artifact("dcc", b"dcc-preview"))
        service = VisualLoopService(self.store, generation_port=generation, judge_port=judge)
        result = service.run_dcc_preview(
            loop["loop_id"], {"scene": "Scene", "camera": "Camera"}, preview_port=preview
        )
        self.assertEqual(result["state"], "completed")
        self.assertEqual(len(preview.calls), 1)
        self.assertEqual(generation.calls, [])


if __name__ == "__main__":
    unittest.main()
