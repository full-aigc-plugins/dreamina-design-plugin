from __future__ import annotations

import copy
import json
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from scripts.shot_analysis_service import REQUIRED_GATES, ShotAnalysisService
from scripts.video_project_store import (
    VersionCommitIndeterminateError,
    VersionReconciliationError,
    VideoProjectStore,
)


FIXTURE = Path(__file__).parent / "fixtures" / "reference_video" / "valid-annotations.json"


class ShotAnalysisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = VideoProjectStore(Path(self.temp.name) / "projects")
        project = self.store.create(title="reference", creative_mode="original_redesign", audio_policy="silent")
        self.project_id = project["project_id"]
        self.store.transition(self.project_id, expected="created", next_state="analyzing", evidence={})
        self.annotations = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.analysis = self._write_analysis()
        self.annotations["machine_fingerprint"] = self.analysis["machine_fingerprint"]
        self.service = ShotAnalysisService(self.store)

    def _write_analysis(self, **changes):
        frames = {}
        for shot_id, start, end in (("S01", 0.0, 4.0), ("S02", 4.0, 8.0)):
            for label, at in (("a", start + .6), ("b", end - .6)):
                frames[f"{shot_id}:{label}"] = {
                    "sha256": ("a" if label == "a" else "b") * 64,
                    "path": f"/private/{shot_id}-{label}.png", "at_seconds": at,
                    "frame_width": 480, "boundary_fingerprint": "c" * 64,
                }
        payload = {
            "schema_version": "1.0", "analysis_id": "an_" + "a" * 24,
            "project_id": self.project_id,
            "source": {
                "schema_version": "1.0", "version": "v001", "project_id": self.project_id,
                "source_sha256": "d" * 64, "size_bytes": 1, "mime_type": "video/mp4",
                "video_codec": "h264", "width": 640, "height": 360, "fps": 25.0,
                "duration_seconds": 8.0, "audio_streams": [], "approved_roots_digest": "e" * 64,
                "staged_path": "/private/source.mp4", "intake_at": "2026-09-14T00:00:00Z"
            },
            "parameters": {"scene_threshold": .3, "min_shot_seconds": .3, "track_hz": 5},
            "cuts": [0.0, 4.0, 8.0], "manual_cuts": [],
            "shots": [
                {"id": "S01", "measured": {"start_seconds": 0.0, "end_seconds": 4.0, "duration_seconds": 4.0, "motion_median": 0.0, "boundary_source": "source_start"}, "semantic": None},
                {"id": "S02", "measured": {"start_seconds": 4.0, "end_seconds": 8.0, "duration_seconds": 4.0, "motion_median": 0.0, "boundary_source": "scene"}, "semantic": None},
            ],
            "track_path": "/private/track.json", "frame_checksums": frames,
            "machine_fingerprint": "f" * 64,
        }
        payload.update(changes)
        return self.store.write_version(self.project_id, "analysis", payload, schema_name="shot_analysis.schema.json")

    @staticmethod
    def gate(result, name):
        return next(gate for gate in result["gates"] if gate["name"] == name)

    def validate(self, annotations=None, version="v001", **kwargs):
        return self.service.validate_and_persist(
            self.project_id, version, annotations or self.annotations, **kwargs
        )

    def test_valid_silent_annotation_records_all_named_gates_and_review_transition(self):
        result = self.validate()
        self.assertEqual({g["name"] for g in result["gates"]}, {
            "schema", "machine_fingerprint", "timeline", "duration", "shot_ids",
            "boundary_provenance", "keyframes", "taxonomy", "frame_specificity",
            "description_dedup", "cast_subjects", "category_evidence", "motion_camera",
            "rhythm_completeness", "transcript_provenance",
        })
        self.assertEqual(self.gate(result, "transcript_provenance")["status"], "skipped")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(self.store.get(self.project_id)["state"], "analysis_review")

    def test_schema_failure_returns_all_fifteen_gates_without_persisting_or_transitioning(self):
        """Catches schema validation escaping before the complete gate report is built."""
        payload = copy.deepcopy(self.annotations)
        payload["shots"][0]["start_seconds"] = 9.0

        result = self.validate(payload)

        self.assertEqual(
            [gate["name"] for gate in result["gates"]],
            [
                "schema", "machine_fingerprint", "timeline", "duration", "shot_ids",
                "boundary_provenance", "keyframes", "taxonomy", "frame_specificity",
                "description_dedup", "cast_subjects", "category_evidence", "motion_camera",
                "rhythm_completeness", "transcript_provenance",
            ],
        )
        self.assertEqual(result["gates"][0], {
            "name": "schema", "status": "failed",
            "violations": ("annotation does not satisfy the closed semantic schema",),
        })
        self.assertEqual(
            [gate["status"] for gate in result["gates"][1:]],
            ["skipped"] * 14,
        )
        self.assertEqual(
            [gate["violations"] for gate in result["gates"][1:]],
            [("schema gate blocked evaluation",)] * 14,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["annotation_version"])
        self.assertFalse((self.store.project_root(self.project_id) / "annotation").exists())
        self.assertEqual(self.store.get(self.project_id)["state"], "analyzing")

    def test_schema_gate_fails_machine_fields_and_low_confidence_without_review_note(self):
        for field, value in (("start_seconds", 9.0), ("motion_median", 3.0), ("boundary_source", "scene")):
            payload = copy.deepcopy(self.annotations)
            payload["shots"][0][field] = value
            with self.subTest(field=field):
                self.assertEqual(self.gate(self.validate(payload), "schema")["status"], "failed")
        payload = copy.deepcopy(self.annotations)
        payload["shots"][0]["confidence"] = .64
        self.assertEqual(self.gate(self.validate(payload), "schema")["status"], "failed")

    def test_machine_fingerprint_gate_passes_exact_binding_and_defeats_stale_binding(self):
        self.assertEqual(self.gate(self.validate(), "machine_fingerprint")["status"], "passed")
        payload = copy.deepcopy(self.annotations); payload["machine_fingerprint"] = "0" * 64
        self.assertEqual(self.gate(self.validate(payload), "machine_fingerprint")["status"], "failed")

    def test_machine_timeline_duration_ids_boundary_and_keyframe_gates_each_defeat_bad_evidence(self):
        cases = {
            "timeline": {"cuts": [0.0, 3.5, 8.0]},
            "duration": {"shots": [{**self.analysis["shots"][0], "measured": {**self.analysis["shots"][0]["measured"], "duration_seconds": 3.0}}, self.analysis["shots"][1]]},
            "shot_ids": {"shots": [{**self.analysis["shots"][0], "id": "S02"}, self.analysis["shots"][1]]},
            "boundary_provenance": {"manual_cuts": [4.0]},
            "keyframes": {"frame_checksums": {k: v for k, v in self.analysis["frame_checksums"].items() if k != "S02:b"}},
        }
        for gate_name, changes in cases.items():
            with self.subTest(gate=gate_name):
                analysis = self._write_analysis(**changes)
                payload = copy.deepcopy(self.annotations); payload["machine_fingerprint"] = analysis["machine_fingerprint"]
                result = self.validate(payload, analysis["version"])
                self.assertEqual(self.gate(result, gate_name)["status"], "failed")

    def test_taxonomy_gate_defeats_unknown_category_while_schema_passes(self):
        payload = copy.deepcopy(self.annotations); payload["shots"][0]["category"] = "viral_magic"
        result = self.validate(payload)
        self.assertEqual(self.gate(result, "schema")["status"], "passed")
        self.assertEqual(self.gate(result, "taxonomy")["status"], "failed")

    def test_frame_specificity_gate_accepts_long_chinese_and_defeats_vague_english(self):
        payload = copy.deepcopy(self.annotations); payload["shots"][0]["frame_description"] = "主持人站在明亮屏幕旁介绍全新的智能控制产品"
        self.assertEqual(self.gate(self.validate(payload), "frame_specificity")["status"], "passed")
        payload["shots"][0]["frame_description"] = "A person stands here"
        self.assertEqual(self.gate(self.validate(payload), "frame_specificity")["status"], "failed")

    def test_description_dedup_gate_defeats_normalized_duplicate_descriptions(self):
        payload = copy.deepcopy(self.annotations)
        payload["shots"][1]["frame_description"] = payload["shots"][0]["frame_description"].upper()
        result = self.validate(payload)
        self.assertEqual(self.gate(result, "description_dedup")["status"], "failed")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.store.get(self.project_id)["state"], "analyzing")

    def test_cast_subject_gate_accepts_consistent_identity_and_defeats_renamed_identity(self):
        self.assertEqual(self.gate(self.validate(), "cast_subjects")["status"], "passed")
        payload = copy.deepcopy(self.annotations)
        payload["shots"][1]["subjects"] = [{"id": "cast_01", "name": "Different person"}]
        self.assertEqual(self.gate(self.validate(payload), "cast_subjects")["status"], "failed")

    def test_category_evidence_gate_defeats_evidence_that_does_not_name_category_intent(self):
        payload = copy.deepcopy(self.annotations); payload["shots"][0]["category_evidence"] = "Visible colors are blue and white."
        self.assertEqual(self.gate(self.validate(payload), "category_evidence")["status"], "failed")

    def test_motion_camera_gate_defeats_push_in_claim_for_static_measurement(self):
        payload = copy.deepcopy(self.annotations); payload["shots"][0]["camera"] = "push_in"
        self.assertEqual(self.gate(self.validate(payload), "motion_camera")["status"], "failed")

    def test_rhythm_completeness_gate_accepts_full_or_empty_and_defeats_partial(self):
        empty = copy.deepcopy(self.annotations)
        for shot in empty["shots"]: shot["rhythm_role"] = None; shot["rhythm_evidence"] = None
        self.assertEqual(self.gate(self.validate(empty), "rhythm_completeness")["status"], "passed")
        partial = copy.deepcopy(self.annotations); partial["shots"][1]["rhythm_role"] = None; partial["shots"][1]["rhythm_evidence"] = None
        self.assertEqual(self.gate(self.validate(partial), "rhythm_completeness")["status"], "failed")

    def test_transcript_provenance_gate_accepts_timed_asr_and_defeats_unprovenanced_asr(self):
        audio_store = VideoProjectStore(Path(self.temp.name) / "audio-projects")
        project = audio_store.create(title="audio", creative_mode="original_redesign", audio_policy="full_redesign")
        audio_store.transition(project["project_id"], expected="created", next_state="analyzing", evidence={})
        analysis = self._write_analysis_for_store(audio_store, project["project_id"])
        service = ShotAnalysisService(audio_store)
        payload = copy.deepcopy(self.annotations); payload["machine_fingerprint"] = analysis["machine_fingerprint"]
        payload["transcript"] = {"policy": "asr", "provider": "whisper-local", "segments": [{"start_seconds": 0.1, "end_seconds": 1.2, "text": "Welcome", "confidence": .91}]}
        self.assertEqual(self.gate(service.validate_and_persist(project["project_id"], "v001", payload), "transcript_provenance")["status"], "passed")
        payload["transcript"]["provider"] = ""
        self.assertEqual(self.gate(service.validate_and_persist(project["project_id"], "v001", payload), "transcript_provenance")["status"], "failed")

    def _write_analysis_for_store(self, store, project_id):
        original_store, original_id = self.store, self.project_id
        self.store, self.project_id = store, project_id
        try: return self._write_analysis()
        finally: self.store, self.project_id = original_store, original_id

    def test_required_skipped_transcript_blocks_non_silent_workflow_but_user_script_skip_is_allowed(self):
        audio_store = VideoProjectStore(Path(self.temp.name) / "script-projects")
        project = audio_store.create(title="audio", creative_mode="original_redesign", audio_policy="full_redesign")
        audio_store.transition(project["project_id"], expected="created", next_state="analyzing", evidence={})
        analysis = self._write_analysis_for_store(audio_store, project["project_id"])
        service = ShotAnalysisService(audio_store)
        payload = copy.deepcopy(self.annotations); payload["machine_fingerprint"] = analysis["machine_fingerprint"]
        result = service.validate_and_persist(project["project_id"], "v001", payload)
        self.assertEqual(self.gate(result, "transcript_provenance")["status"], "skipped")
        self.assertEqual(result["status"], "failed")
        payload["transcript"] = {"policy": "user_supplied_script", "provider": "user", "segments": []}
        result = service.validate_and_persist(project["project_id"], "v001", payload)
        self.assertEqual(self.gate(result, "transcript_provenance")["status"], "skipped")
        self.assertEqual(result["status"], "passed")

    def test_annotation_version_is_bound_to_analysis_version_and_fingerprint(self):
        result = self.validate()
        persisted = json.loads((self.store.project_root(self.project_id) / "annotation" / "v001.json").read_text(encoding="utf-8"))
        self.assertEqual(result["annotation_version"], "v001")
        self.assertEqual(persisted["analysis_version"], "v001")
        self.assertEqual(persisted["machine_fingerprint"], self.analysis["machine_fingerprint"])

    def test_unknown_analysis_version_is_rejected_without_fallback(self):
        with self.assertRaises(KeyError): self.validate(version="v999")

    def test_post_replace_annotation_failure_propagates_once_without_allocating_v002(self):
        """Catches a downstream retry after an indeterminate committed annotation write."""
        calls = 0
        real_fsync = os.fsync

        def fail_directory_fsync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected annotation directory fsync failure")
            real_fsync(descriptor)

        with patch("scripts.video_project_store.os.fsync", side_effect=fail_directory_fsync):
            with self.assertRaises(VersionCommitIndeterminateError) as raised:
                self.validate()

        annotation_root = self.store.project_root(self.project_id) / "annotation"
        self.assertEqual(raised.exception.family, "annotation")
        self.assertEqual(raised.exception.version, "v001")
        self.assertEqual([path.name for path in annotation_root.glob("v*.json")], ["v001.json"])
        self.assertEqual(self.store.get(self.project_id)["state"], "analyzing")

    def test_retry_reconciles_exact_indeterminate_annotation_without_allocating_v002(self):
        """Catches a genuine retry allocating a fresh annotation version."""
        calls = 0
        real_fsync = os.fsync

        def fail_directory_fsync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected annotation directory fsync failure")
            real_fsync(descriptor)

        with patch("scripts.video_project_store.os.fsync", side_effect=fail_directory_fsync):
            with self.assertRaises(VersionCommitIndeterminateError) as raised:
                self.validate()

        result = self.validate(indeterminate_commit=raised.exception)

        annotation_root = self.store.project_root(self.project_id) / "annotation"
        self.assertEqual(result["annotation_version"], "v001")
        self.assertEqual(result["status"], "passed")
        self.assertEqual([path.name for path in annotation_root.glob("v*.json")], ["v001.json"])
        self.assertEqual(self.store.get(self.project_id)["state"], "analysis_review")

    def test_retry_with_changed_annotation_stays_typed_blocking_and_does_not_transition(self):
        """Catches reconciliation accepting input other than the exact committed document."""
        calls = 0
        real_fsync = os.fsync

        def fail_directory_fsync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected annotation directory fsync failure")
            real_fsync(descriptor)

        with patch("scripts.video_project_store.os.fsync", side_effect=fail_directory_fsync):
            with self.assertRaises(VersionCommitIndeterminateError) as raised:
                self.validate()
        changed = copy.deepcopy(self.annotations)
        changed["shots"][0]["frame_description"] = (
            "A presenter stands beside a different illuminated control display."
        )

        with self.assertRaises(VersionReconciliationError):
            self.validate(changed, indeterminate_commit=raised.exception)
        annotation_root = self.store.project_root(self.project_id) / "annotation"
        self.assertEqual([path.name for path in annotation_root.glob("v*.json")], ["v001.json"])
        self.assertEqual(self.store.get(self.project_id)["state"], "analyzing")


if __name__ == "__main__":
    unittest.main()
