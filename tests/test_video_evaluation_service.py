from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.video_evaluation_service import (
    EvaluationContractError,
    VideoEvaluationService,
)


SEMANTIC_NAMES = {
    "intent", "composition", "identity_continuity", "camera_behavior",
    "rhythm_function", "temporal_defects", "source_copying", "subtitle_safe_area",
}


def binding() -> dict:
    return {"project_id": "vp_" + "1" * 24, "batch_version": "v001", "shot_id": "S01",
            "attempt": 1, "artifact_sha256": "a" * 64, "design_version": "v001",
            "design_fingerprint": "b" * 64, "quote_fingerprint": "c" * 64,
            "allowance_id": "ba_" + "d" * 32}


def semantic(status: str = "passed") -> dict:
    return {"binding": binding(), "gates": [{"name": name, "status": status, "evidence": f"reviewed {name}"}
                      for name in sorted(SEMANTIC_NAMES)]}


def measured(status: str = "passed") -> dict:
    names = {"artifact_integrity", "dimensions", "codec", "duration", "aspect_ratio",
             "frame_readability", "start_anchor", "end_anchor"}
    return {"binding": binding(), "gates": [{"name": name, "status": status, "evidence": f"measured {name}"}
                      for name in sorted(names)]}


def allowance_state(requests: list[dict]) -> dict:
    value = binding()
    value.pop("shot_id"); value.pop("attempt"); value.pop("artifact_sha256")
    value["requests"] = requests
    return value


class VideoEvaluationServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = VideoEvaluationService()
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clip = Path(self.tmp.name) / "clip.mp4"
        self.clip.write_bytes(b"\0\0\0\x18ftypisom" + b"x" * 20)
        digest = hashlib.sha256(self.clip.read_bytes()).hexdigest()
        bound = binding(); bound["artifact_sha256"] = digest
        self.artifact = {
            **bound, "path": str(self.clip), "sha256": digest, "verified_sha256": digest,
            "probe": {"width": 1280, "height": 720, "codec": "h264", "duration_seconds": 4.0},
            "frames": {"readable": True, "start_anchor": True, "end_anchor": True},
        }
        self.shot = {**bound, "id": "S01", "width": 1280, "height": 720, "codec": "h264",
                     "duration_seconds": 4.0, "aspect_ratio": "16:9"}

    def test_duration_dimension_codec_and_anchor_gates_use_measured_media(self):
        result = self.service.measure_clip(self.artifact, self.shot)
        self.assertEqual({gate["name"] for gate in result["gates"]}, {
            "artifact_integrity", "dimensions", "codec", "duration", "aspect_ratio",
            "frame_readability", "start_anchor", "end_anchor",
        })
        self.assertTrue(all(gate["status"] == "passed" for gate in result["gates"]))

    def test_missing_or_conflicting_media_evidence_is_not_invented_as_pass(self):
        missing = copy.deepcopy(self.artifact); del missing["probe"]["duration_seconds"]
        result = self.service.measure_clip(missing, self.shot)
        self.assertEqual(next(g for g in result["gates"] if g["name"] == "duration")["status"], "unavailable")
        conflicting = copy.deepcopy(self.artifact); conflicting["verified_sha256"] = "f" * 64
        result = self.service.measure_clip(conflicting, self.shot)
        self.assertEqual(next(g for g in result["gates"] if g["name"] == "artifact_integrity")["status"], "failed")

    def test_semantic_evaluator_cannot_overwrite_measured_gate(self):
        payload = semantic(); payload["duration"] = {"status": "passed"}
        with self.assertRaises(EvaluationContractError):
            self.service.validate_semantic_evaluation(payload)

    def test_semantic_contract_requires_each_gate_once_and_closed_status(self):
        for mutate in (lambda p: p["gates"].pop(), lambda p: p["gates"].append(p["gates"][0])):
            payload = semantic(); mutate(payload)
            with self.assertRaises(EvaluationContractError):
                self.service.validate_semantic_evaluation(payload)
        payload = semantic(); payload["gates"][0]["status"] = "maybe"
        with self.assertRaises(EvaluationContractError):
            self.service.validate_semantic_evaluation(payload)

    def test_binding_conflict_requires_manual_review(self):
        other = semantic(); other["binding"]["quote_fingerprint"] = "e" * 64
        self.assertEqual(self.service.decide(measured(), other, {})["action"], "manual_review")

    def test_all_required_pass_is_accepted(self):
        self.assertEqual(self.service.decide(measured(), semantic(), {})["action"], "accepted")

    def test_each_semantic_failure_maps_only_to_closed_repair(self):
        expected = {
            "identity_continuity": "identity_consistency", "camera_behavior": "camera_match",
            "composition": "camera_match", "rhythm_function": "camera_match",
            "temporal_defects": "temporal_stability", "source_copying": "remove_text",
            "subtitle_safe_area": "remove_text",
        }
        for gate_name in SEMANTIC_NAMES:
            with self.subTest(gate=gate_name):
                payload = semantic()
                next(g for g in payload["gates"] if g["name"] == gate_name)["status"] = "failed"
                directive = expected.get(gate_name)
                allowance = allowance_state([])
                if directive:
                    allowance["requests"] = [{"shot_id": "S01", "attempt": 2,
                        "repair_directive": directive, "request_fingerprint": "a" * 64, "state": "available"}]
                result = self.service.decide(measured(), payload, allowance)
                self.assertEqual(result["action"], "retry" if directive else "manual_review")
                if directive:
                    self.assertEqual(result["repair_directive"], directive)

    def test_retry_uses_only_exact_next_remaining_prequoted_fingerprint(self):
        payload = semantic()
        next(g for g in payload["gates"] if g["name"] == "identity_continuity")["status"] = "failed"
        allowance = allowance_state([
            {"shot_id": "S02", "attempt": 2, "repair_directive": "identity_consistency", "request_fingerprint": "b" * 64, "state": "available"},
            {"shot_id": "S01", "attempt": 3, "repair_directive": "identity_consistency", "request_fingerprint": "c" * 64, "state": "available"},
            {"shot_id": "S01", "attempt": 2, "repair_directive": "identity_consistency", "request_fingerprint": "a" * 64, "state": "available"},
        ])
        decision = self.service.decide(measured(), payload, allowance)
        self.assertEqual(decision["action"], "retry")
        self.assertEqual(decision["request_fingerprint"], "a" * 64)

    def test_retry_rejects_foreign_allowance_binding(self):
        payload = semantic()
        next(g for g in payload["gates"] if g["name"] == "identity_continuity")["status"] = "failed"
        allowance = allowance_state([{
            "shot_id": "S01", "attempt": 2, "repair_directive": "identity_consistency",
            "request_fingerprint": "a" * 64, "state": "available"}])
        allowance["allowance_id"] = "ba_" + "e" * 32
        self.assertEqual(self.service.decide(measured(), payload, allowance)["action"], "manual_review")

    def test_unavailable_disagreement_exhaustion_and_mixed_directives_require_review(self):
        unavailable = measured(); unavailable["gates"][0]["status"] = "unavailable"
        self.assertEqual(self.service.decide(unavailable, semantic(), {})["action"], "manual_review")
        for state in ("consumed", "exhausted"):
            payload = semantic()
            next(g for g in payload["gates"] if g["name"] == "camera_behavior")["status"] = "failed"
            allowance = allowance_state([{"shot_id": "S01", "attempt": 2,
                "repair_directive": "camera_match", "request_fingerprint": "a" * 64, "state": state}])
            self.assertEqual(self.service.decide(measured(), payload, allowance)["action"], "manual_review")
        mixed = semantic()
        next(g for g in mixed["gates"] if g["name"] == "identity_continuity")["status"] = "failed"
        next(g for g in mixed["gates"] if g["name"] == "temporal_defects")["status"] = "failed"
        self.assertEqual(self.service.decide(measured(), mixed, {})["action"], "manual_review")


if __name__ == "__main__":
    unittest.main()
