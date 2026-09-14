from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.json_contracts import validate_contract
from scripts.video_evaluation_service import EvaluationContractError, VideoEvaluationService
from tests.test_video_batch_allowance import RecordingApprover, make_allowances, make_quote

MEASURED = ("artifact_integrity", "dimensions", "codec", "duration", "aspect_ratio",
            "frame_readability", "start_anchor", "end_anchor")
SEMANTIC = ("intent", "composition", "identity_continuity", "camera_behavior",
            "rhythm_function", "temporal_defects", "source_copying", "subtitle_safe_area")


def semantic(binding: dict, status: str = "passed") -> dict:
    return {"binding": copy.deepcopy(binding), "gates": {
        name: {"status": status, "evidence": f"reviewed {name}"} for name in SEMANTIC}}


class VideoEvaluationServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = VideoEvaluationService()
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clip = Path(self.tmp.name) / "clip.mp4"
        self.clip.write_bytes(b"\0\0\0\x18ftypisom" + b"x" * 20)
        digest = hashlib.sha256(self.clip.read_bytes()).hexdigest()
        self.quote = make_quote()
        self.binding = {"project_id": self.quote["project_id"], "batch_version": self.quote["quote_version"],
                        "shot_id": "S01", "attempt": 1, "artifact_sha256": digest,
                        "design_version": self.quote["design_version"], "design_fingerprint": self.quote["design_fingerprint"],
                        "quote_fingerprint": self.quote["quote_fingerprint"], "allowance_id": "ba_" + "d" * 32}
        self.artifact = {**self.binding, "path": str(self.clip), "sha256": digest, "verified_sha256": digest,
                         "mime_type": "video/mp4", "size_bytes": self.clip.stat().st_size,
                         "provenance": "externally-queried",
                         "probe": {"width": 1280, "height": 720, "codec": "h264", "duration_seconds": 4.0},
                         "frames": {"readable": True, "start_anchor": True, "end_anchor": True}}
        self.shot = {**self.binding, "id": "S01", "width": 1280, "height": 720,
                     "codec": "h264", "duration_seconds": 4.0, "aspect_ratio": "16:9"}

    def test_measured_gates_are_one_closed_keyed_object(self):
        result = self.service.measure_clip(self.artifact, self.shot)
        self.assertEqual(set(result["gates"]), set(MEASURED))
        self.assertTrue(all(gate["status"] == "passed" for gate in result["gates"].values()))

    def test_semantic_gate_object_rejects_missing_extra_and_wrong_domain(self):
        missing = semantic(self.binding); missing["gates"].pop("intent")
        extra = semantic(self.binding); extra["gates"]["watermark"] = {"status": "passed", "evidence": "x"}
        wrong = semantic(self.binding); wrong["gates"]["duration"] = wrong["gates"].pop("intent")
        for payload in (missing, extra, wrong):
            with self.subTest(keys=sorted(payload["gates"])), self.assertRaises(EvaluationContractError):
                self.service.validate_semantic_evaluation(payload)

    def test_semantic_binding_conflict_returns_manual_review_receipt(self):
        payload = semantic(self.binding); payload["binding"]["quote_fingerprint"] = "f" * 64
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        receipt = self.service.evaluate(self.artifact, self.shot, payload, allowance=allowance, quote=self.quote,
            evaluation_id="eval_conflict", evaluator={"provider": "codex", "model": "test",
            "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(receipt["decision"], {"action": "manual_review"})
        self.assertTrue(all(gate["status"] == "unavailable" for gate in receipt["semantic_gates"].values()))

    def test_missing_measured_evidence_is_unavailable(self):
        artifact = copy.deepcopy(self.artifact); artifact["probe"].pop("duration_seconds")
        self.assertEqual(self.service.measure_clip(artifact, self.shot)["gates"]["duration"]["status"], "unavailable")
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        receipt = self.service.evaluate(artifact, self.shot, semantic(self.binding), allowance=allowance,
            quote=self.quote, evaluation_id="eval_missing", evaluator={"provider": "codex", "model": "test",
            "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(receipt["decision"], {"action": "manual_review"})

    def test_real_allowance_and_quote_select_only_exact_available_retry(self):
        allowance_service = make_allowances(Path(self.tmp.name) / "allowance-root", Path(self.tmp.name) / "seal.key")
        allowance_id = allowance_service.activate(self.quote, RecordingApprover())
        self.binding["allowance_id"] = allowance_id; self.artifact.update(self.binding); self.shot.update(self.binding)
        payload = semantic(self.binding); payload["gates"]["temporal_defects"]["status"] = "failed"
        evaluator = {"provider": "codex", "model": "test-model", "evaluated_at": "2026-09-14T02:00:00Z"}
        receipt = self.service.evaluate(self.artifact, self.shot, payload,
            allowance=allowance_service.get(allowance_id), quote=self.quote,
            evaluation_id="eval_real_1", evaluator=evaluator)
        retry = self.quote["items"][0]["attempts"][1]
        self.assertEqual(receipt["decision"], {"action": "retry", "repair_directive": "temporal_stability",
                                               "request_fingerprint": retry["request_fingerprint"]})
        validate_contract(receipt, "shot_evaluation.schema.json")
        retry_reservation = allowance_service.reserve(allowance_id, shot_id="S01", attempt=2,
                                                       request_fingerprint=retry["request_fingerprint"])
        blocked = self.service.evaluate(self.artifact, self.shot, payload,
            allowance=allowance_service.get(allowance_id), quote=self.quote,
            evaluation_id="eval_real_2", evaluator={**evaluator, "evaluated_at": "2026-09-14T02:00:01Z"})
        self.assertEqual(blocked["decision"], {"action": "manual_review"})
        initial = self.quote["items"][0]["attempts"][0]
        initial_reservation = allowance_service.reserve(allowance_id, shot_id="S01", attempt=1,
            request_fingerprint=initial["request_fingerprint"])
        allowance_service.commit(retry_reservation["reservation_id"], "submit_retry")
        allowance_service.commit(initial_reservation["reservation_id"], "submit_initial")
        exhausted = self.service.evaluate(self.artifact, self.shot, payload,
            allowance=allowance_service.get(allowance_id), quote=self.quote,
            evaluation_id="eval_real_3", evaluator={**evaluator, "evaluated_at": "2026-09-14T02:00:02Z"})
        self.assertEqual(exhausted["decision"], {"action": "manual_review"})

    def test_complete_receipt_keeps_artifact_gate_and_evaluator_evidence(self):
        allowance = {"allowance_id": self.binding["allowance_id"], "project_id": self.binding["project_id"],
                     "quote_version": self.binding["batch_version"], "quote_fingerprint": self.binding["quote_fingerprint"],
                     "design_version": self.binding["design_version"], "design_fingerprint": self.binding["design_fingerprint"],
                     "state": "active", "requests": [], "reservations": []}
        receipt = self.service.evaluate(self.artifact, self.shot, semantic(self.binding), allowance=allowance,
            quote=self.quote, evaluation_id="eval_complete", evaluator={"provider": "codex", "model": "test-model",
            "evaluated_at": "2026-09-14T02:00:00Z"})
        self.assertEqual(set(receipt), {"schema_version", "evaluation_id", "binding", "artifact_evidence",
            "measured_gates", "semantic_gates", "failed_gates", "decision", "evaluator", "evaluation_fingerprint"})
        self.assertEqual(receipt["decision"], {"action": "accepted"})
        validate_contract(receipt, "shot_evaluation.schema.json")


if __name__ == "__main__": unittest.main()
