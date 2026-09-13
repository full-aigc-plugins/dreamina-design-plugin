from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.json_contracts import ContractValidationError, canonical_fingerprint
from scripts.video_project_store import (
    VersionCommitIndeterminateError,
    VersionReconciliationError,
    VideoProjectStore,
)
from scripts.video_rights_service import VideoRightsService
from scripts.video_redesign_service import (
    REUSE_DIMENSIONS,
    OriginalityPolicyError,
    RedesignBindingError,
    VideoRedesignService,
)


class VideoRedesignServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = VideoProjectStore(Path(self.temp.name) / "projects")
        project = self.store.create(
            title="reference", creative_mode="original_redesign", audio_policy="silent"
        )
        self.project_id = project["project_id"]
        self.fingerprint = "b" * 64
        self._persist_analysis_and_annotation()
        self.redesign = VideoRedesignService(self.store)

    def _persist_analysis_and_annotation(self) -> None:
        source = {
            "schema_version": "1.0", "version": "v001", "project_id": self.project_id,
            "source_sha256": "a" * 64, "size_bytes": 12, "mime_type": "video/mp4",
            "video_codec": "h264", "width": 1280, "height": 720, "fps": 24.0,
            "duration_seconds": 4.0, "audio_streams": [], "approved_roots_digest": "c" * 64,
            "staged_path": "/private/source.mp4", "intake_at": "2026-09-14T00:00:00Z",
        }
        analysis = {
            "schema_version": "1.0", "analysis_id": "an_" + "d" * 24,
            "project_id": self.project_id, "source": source,
            "parameters": {"scene_threshold": .3, "min_shot_seconds": .3, "track_hz": 5},
            "cuts": [0.0, 4.0], "manual_cuts": [],
            "shots": [{"id": "S01", "measured": {"start_seconds": 0.0, "end_seconds": 4.0, "duration_seconds": 4.0, "motion_median": 0.0, "boundary_source": "source_start"}, "semantic": None}],
            "track_path": "/private/track.json",
            "frame_checksums": {label: {"sha256": "e" * 64, "path": "/private/frame.png", "at_seconds": at, "frame_width": 480, "boundary_fingerprint": "f" * 64} for label, at in (("S01:a", .5), ("S01:b", 3.5))},
            "machine_fingerprint": self.fingerprint,
        }
        self.store.write_version(self.project_id, "analysis", analysis, schema_name="shot_analysis.schema.json")
        annotation = {
            "schema_version": "1.0", "analysis_version": "v001", "machine_fingerprint": self.fingerprint,
            "shots": [{"id": "S01", "shot_size": "wide", "category": "subject", "category_evidence": "subject person", "camera": "static", "frame_description": "A clearly described presenter standing inside a bright modern studio", "rhythm_role": None, "rhythm_evidence": None, "subjects": [], "on_screen_text": [], "dialogue": [], "narration": [], "music": [], "sound": [], "confidence": .9, "review_note": ""}],
            "transcript": None,
        }
        self.store.write_version(self.project_id, "annotation", annotation, schema_name="shot_annotation.schema.json")

    def payload(self, **changes):
        payload = {
            "schema_version": "1.0", "creative_mode": "original_redesign",
            "machine_fingerprint": self.fingerprint,
            "preserve": ["timing", "shot_sizes", "camera_moves", "rhythm", "transitions", "audio_beats"],
            "replacements": {key: f"new {key}" for key in ("likeness", "voice", "dialogue", "music", "brand", "artwork", "settings", "costume", "distinctive_props")},
            "required_media": ["video"], "purpose": "public commercial campaign",
            "audience": "public", "territory": "worldwide",
            "format": "short_video", "target_duration_seconds": 4.0,
            "aspect_ratio": "16:9", "platform": "web", "concept": "A new original launch story",
            "cast": ["new presenter"], "settings": ["new studio"], "palette": ["blue"],
            "visual_style": "clean editorial", "dialogue": [], "narration": [], "music": "new licensed score",
            "sound_intent": "new sound design", "shots": [{"id": "S01", "prompt": "new presenter in a new studio"}],
            "continuity": ["new presenter remains consistent"], "author": "user",
        }
        payload.update(changes)
        if payload["creative_mode"] == "authorized_replication":
            payload.pop("replacements", None)
        return payload

    def test_original_redesign_forbids_source_face_voice_brand_dialogue_music_reuse(self):
        for dimension in ("likeness", "voice", "brand", "dialogue", "music", "artwork", "distinctive_props"):
            with self.subTest(dimension=dimension), self.assertRaises(OriginalityPolicyError):
                self.redesign.prepare_candidate(self.project_id, "v001", self.payload(preserve=[dimension]))

    def test_original_redesign_requires_explicit_expressive_replacements(self):
        for dimension in ("likeness", "voice", "dialogue", "music", "brand", "artwork", "settings", "costume", "distinctive_props"):
            payload = self.payload()
            del payload["replacements"][dimension]
            with self.subTest(dimension=dimension), self.assertRaises(OriginalityPolicyError):
                self.redesign.prepare_candidate(self.project_id, "v001", payload)

    def test_redesign_cannot_change_measured_analysis(self):
        with self.assertRaises(RedesignBindingError):
            self.redesign.prepare_candidate(self.project_id, "v001", self.payload(machine_fingerprint="0" * 64))

    def test_analysis_and_annotation_reads_reject_symlink_and_replacement_races(self):
        project_root = self.store.project_root(self.project_id)
        for family in ("analysis", "annotation"):
            with self.subTest(family=family):
                target = project_root / family / "v001.json"
                original = target.read_bytes()
                outside = Path(self.temp.name) / f"{family}-outside.json"
                outside.write_bytes(original)
                target.unlink()
                target.symlink_to(outside)
                with self.assertRaises(RedesignBindingError):
                    self.redesign.prepare_candidate(self.project_id, "v001", self.payload())
                target.unlink()
                target.write_bytes(original)
                target.chmod(0o600)

        from scripts.json_contracts import validate_contract as real_validate

        for family, schema_name in (("analysis", "shot_analysis.schema.json"), ("annotation", "shot_annotation.schema.json")):
            target = project_root / family / "v001.json"
            replacement = target.with_name(".replacement.json")
            replacement.write_bytes(target.read_bytes())
            replacement.chmod(0o600)
            replaced = False

            def replace_after_validation(document, current_schema):
                nonlocal replaced
                real_validate(document, current_schema)
                if current_schema == schema_name and not replaced:
                    os.replace(replacement, target)
                    replaced = True

            with self.subTest(family=family), patch(
                "scripts.video_project_store.validate_contract", side_effect=replace_after_validation
            ):
                with self.assertRaises(RedesignBindingError):
                    self.redesign.prepare_candidate(self.project_id, "v001", self.payload())
            self.assertTrue(replaced)

    def test_candidate_payload_is_closed_before_rights_confirmation(self):
        with self.assertRaises(ContractValidationError):
            self.redesign.prepare_candidate(
                self.project_id, "v001", self.payload(unreviewed_extension=True)
            )

    def test_candidate_is_not_persisted_and_mutation_invalidates_commit(self):
        candidate = self.redesign.prepare_candidate(self.project_id, "v001", self.payload())
        self.assertFalse((self.store.project_root(self.project_id) / "redesign").exists())
        candidate["payload"]["concept"] = "mutated after authorization boundary"
        with self.assertRaises(RedesignBindingError):
            self.redesign.commit_version(candidate, rights_receipt_id=None)

    def test_prepared_candidate_is_private_and_content_addressed(self):
        candidate = self.redesign.prepare_candidate(self.project_id, "v001", self.payload())
        root = self.store.project_root(self.project_id) / "prepared_candidate"
        target = root / f"{candidate['design_fingerprint']}.json"
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), candidate)

    def test_prepared_candidate_publication_rejects_project_directory_swap(self):
        project_root = self.store.project_root(self.project_id)
        detached = project_root.parent / f"{self.project_id}.detached"
        real_link = os.link

        def replace_project_before_publish(*args, **kwargs):
            os.rename(project_root, detached)
            project_root.mkdir(mode=0o700)
            return real_link(*args, **kwargs)

        with patch("scripts.video_project_store.os.link", side_effect=replace_project_before_publish):
            with self.assertRaises(RedesignBindingError):
                self.redesign.prepare_candidate(self.project_id, "v001", self.payload())
        self.assertFalse((project_root / "prepared_candidate").exists())

    def test_recomputed_candidate_cannot_bypass_originality_policy_at_commit(self):
        candidate = self.redesign.prepare_candidate(self.project_id, "v001", self.payload())
        candidate["payload"]["preserve"] = ["likeness"]
        candidate["payload"]["replacements"]["voice"] = "   "
        candidate["similarity_audit"] = self.redesign._audit(["likeness"])
        core = {key: value for key, value in candidate.items() if key != "design_fingerprint"}
        candidate["design_fingerprint"] = canonical_fingerprint(core)
        with self.assertRaises(OriginalityPolicyError):
            self.redesign.commit_version(candidate, rights_receipt_id=None)

    def test_similarity_audit_partitions_every_dimension_exactly_once(self):
        candidate = self.redesign.prepare_candidate(self.project_id, "v001", self.payload())
        design = self.redesign.commit_version(candidate, rights_receipt_id=None)
        preserved = design["similarity_audit"]["preserved"]
        replaced = design["similarity_audit"]["replaced"]
        self.assertEqual(set(preserved) | set(replaced), REUSE_DIMENSIONS)
        self.assertFalse(set(preserved) & set(replaced))
        self.assertEqual(len(preserved) + len(replaced), len(REUSE_DIMENSIONS))

    def test_parent_version_is_allocated_numerically_inside_atomic_store_write(self):
        candidate = self.redesign.prepare_candidate(self.project_id, "v001", self.payload())
        results = []
        failures = []

        def commit():
            try:
                results.append(self.redesign.commit_version(copy.deepcopy(candidate), None))
            except BaseException as exc:
                failures.append(exc)

        threads = [threading.Thread(target=commit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        by_version = {item["version"]: item for item in results}
        self.assertIsNone(by_version["v001"]["parent_version"])
        self.assertEqual(by_version["v002"]["parent_version"], "v001")

        family = self.store.project_root(self.project_id) / "redesign"
        marker = family / "v999.json"
        marker.write_text("{}", encoding="utf-8")
        marker.chmod(0o600)
        next_design = self.redesign.commit_version(copy.deepcopy(candidate), None)
        self.assertEqual(next_design["version"], "v1000")
        self.assertEqual(next_design["parent_version"], "v999")

    def test_indeterminate_redesign_commit_reconciles_without_allocating_v002(self):
        candidate = self.redesign.prepare_candidate(self.project_id, "v001", self.payload())
        original = self.store._atomic_write
        captured = None

        def fail_after_publish(path, payload, *, indeterminate_error=None):
            nonlocal captured
            original(path, payload, indeterminate_error=None)
            captured = indeterminate_error
            raise indeterminate_error

        self.store._atomic_write = fail_after_publish
        with self.assertRaises(VersionCommitIndeterminateError):
            self.redesign.commit_version(candidate, rights_receipt_id=None)
        self.store._atomic_write = original
        design = self.redesign.commit_version(
            candidate, rights_receipt_id=None, indeterminate_commit=captured
        )
        self.assertEqual(design["version"], "v001")
        versions = self.store.project_root(self.project_id) / "redesign"
        self.assertEqual([path.name for path in versions.glob("v*.json")], ["v001.json"])

    def test_indeterminate_redesign_retry_rejects_tampered_candidate_and_metadata(self):
        candidate = self.redesign.prepare_candidate(self.project_id, "v001", self.payload())
        original = self.store._atomic_write
        captured = None

        def fail_after_publish(path, payload, *, indeterminate_error=None):
            nonlocal captured
            original(path, payload, indeterminate_error=None)
            captured = indeterminate_error
            raise indeterminate_error

        self.store._atomic_write = fail_after_publish
        with self.assertRaises(VersionCommitIndeterminateError):
            self.redesign.commit_version(candidate, rights_receipt_id=None)
        self.store._atomic_write = original

        tampered = copy.deepcopy(candidate)
        tampered["payload"]["concept"] = "changed after the uncertain commit"
        with self.assertRaises(RedesignBindingError):
            self.redesign.commit_version(
                tampered, rights_receipt_id=None, indeterminate_commit=captured
            )
        wrong = VersionCommitIndeterminateError(
            project_id=self.project_id, family="rights_receipt", version=captured.version,
            path=captured.path, payload_fingerprint=captured.payload_fingerprint,
        )
        with self.assertRaises(VersionReconciliationError):
            self.redesign.commit_version(
                candidate, rights_receipt_id=None, indeterminate_commit=wrong
            )
        versions = self.store.project_root(self.project_id) / "redesign"
        self.assertEqual([path.name for path in versions.glob("v*.json")], ["v001.json"])

    def test_replication_cannot_commit_without_exact_bound_receipt(self):
        project = self.store.create(title="replica", creative_mode="authorized_replication", audio_policy="silent")
        other = VideoRedesignService(self.store)
        # Copy valid immutable evidence into the second project with corrected identities.
        self.project_id = project["project_id"]
        self._persist_analysis_and_annotation()
        candidate = other.prepare_candidate(self.project_id, "v001", self.payload(creative_mode="authorized_replication", preserve=list(REUSE_DIMENSIONS)))
        with self.assertRaises(RedesignBindingError):
            other.commit_version(candidate, rights_receipt_id=None)

    def test_replication_commits_only_after_candidate_bound_native_receipt(self):
        project = self.store.create(title="replica", creative_mode="authorized_replication", audio_policy="silent")
        self.project_id = project["project_id"]
        self._persist_analysis_and_annotation()
        candidate = self.redesign.prepare_candidate(
            self.project_id, "v001",
            self.payload(creative_mode="authorized_replication", preserve=["likeness", "voice"]),
        )

        class Confirmer:
            def confirm_video_rights(self, request): return "native-video-rights-confirmed"

        rights = VideoRightsService(self.store, Confirmer())
        assertion = {
            "declarant": "holder@example.test", "rights_basis": "written license",
            "evidence": [{"reference": "license-record", "sha256": "c" * 64}],
            "allowed_media": ["video"], "allowed_reuse": ["likeness", "voice"],
            "purpose": "public commercial campaign", "audience": "public", "territory": "worldwide",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        source = {"project_id": self.project_id, "source_sha256": "a" * 64}
        receipt = rights.record_assertion(self.project_id, source, candidate, assertion)
        design = self.redesign.commit_version(candidate, receipt["receipt_id"])
        self.assertEqual(design["rights_receipt_id"], receipt["receipt_id"])
        self.assertEqual(design["design_fingerprint"], candidate["design_fingerprint"])

    def test_replication_commit_always_enforces_media_purpose_audience_and_territory(self):
        project = self.store.create(title="replica", creative_mode="authorized_replication", audio_policy="silent")
        self.project_id = project["project_id"]
        self._persist_analysis_and_annotation()
        candidate = self.redesign.prepare_candidate(
            self.project_id, "v001",
            self.payload(creative_mode="authorized_replication", preserve=["likeness"]),
        )

        class Confirmer:
            def confirm_video_rights(self, request): return "native-video-rights-confirmed"

        rights = VideoRightsService(self.store, Confirmer())
        base = {
            "declarant": "holder@example.test", "rights_basis": "written license",
            "evidence": [{"reference": "license-record", "sha256": "c" * 64}],
            "allowed_media": ["video"], "allowed_reuse": ["likeness"],
            "purpose": "public commercial campaign", "audience": "public",
            "territory": "worldwide", "expires_at": "2099-01-01T00:00:00Z",
        }
        mismatches = (
            {"allowed_media": ["text"]},
            {"purpose": "internal evaluation"},
            {"audience": "employees"},
            {"territory": "US"},
        )
        source = {"project_id": self.project_id, "source_sha256": "a" * 64}
        for changes in mismatches:
            assertion = {**base, **changes}
            receipt = rights.record_assertion(self.project_id, source, candidate, assertion)
            with self.subTest(changes=changes), self.assertRaises(RedesignBindingError):
                self.redesign.commit_version(candidate, receipt["receipt_id"])
        self.assertFalse((self.store.project_root(self.project_id) / "redesign").exists())

    def test_rights_authorization_rejects_symlink_and_parent_replacement(self):
        project = self.store.create(title="replica", creative_mode="authorized_replication", audio_policy="silent")
        self.project_id = project["project_id"]
        self._persist_analysis_and_annotation()
        candidate = self.redesign.prepare_candidate(
            self.project_id, "v001",
            self.payload(creative_mode="authorized_replication", preserve=["likeness"]),
        )

        class Confirmer:
            def confirm_video_rights(self, request): return "native-video-rights-confirmed"

        assertion = {
            "declarant": "holder@example.test", "rights_basis": "written license",
            "evidence": [{"reference": "license-record", "sha256": "c" * 64}],
            "allowed_media": ["video"], "allowed_reuse": ["likeness"],
            "purpose": "public commercial campaign", "audience": "public",
            "territory": "worldwide", "expires_at": "2099-01-01T00:00:00Z",
        }
        source = {"project_id": self.project_id, "source_sha256": "a" * 64}
        receipt = VideoRightsService(self.store, Confirmer()).record_assertion(
            self.project_id, source, candidate, assertion
        )
        family = self.store.project_root(self.project_id) / "rights_receipt"
        target = family / "v001.json"
        outside = Path(self.temp.name) / "outside.json"
        outside.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaises(RedesignBindingError):
            self.redesign.commit_version(candidate, receipt["receipt_id"])

        target.unlink()
        target.write_bytes(outside.read_bytes())
        target.chmod(0o600)
        real_load = json.load
        replaced = False

        def replace_parent(handle):
            nonlocal replaced
            document = real_load(handle)
            if not replaced:
                old = family.with_name("rights_receipt-old")
                os.rename(family, old)
                family.mkdir(mode=0o700)
                replacement = family / "v001.json"
                replacement.write_bytes(outside.read_bytes())
                replacement.chmod(0o600)
                replaced = True
            return document

        with patch("scripts.video_project_store.json.load", side_effect=replace_parent):
            with self.assertRaises(RedesignBindingError):
                self.redesign.commit_version(candidate, receipt["receipt_id"])
        self.assertTrue(replaced)


if __name__ == "__main__":
    unittest.main()
