from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.json_contracts import ContractValidationError, canonical_fingerprint
from scripts.video_project_store import (
    VersionCommitIndeterminateError,
    VersionReconciliationError,
    VideoProjectStore,
)
from scripts.video_redesign_service import VideoRedesignService
from scripts.video_rights_service import RightsScopeError, VideoRightsService


class Confirmer:
    def __init__(self) -> None: self.requests = []
    def confirm_video_rights(self, request):
        self.requests.append(copy.deepcopy(request))
        return "native-video-rights-confirmed"


class MutatingConfirmer(Confirmer):
    def confirm_video_rights(self, request):
        self.requests.append(copy.deepcopy(request))
        request["evidence"][0]["reference"] = "mutated"
        return "native-video-rights-confirmed"


class VideoRightsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = VideoProjectStore(Path(self.temp.name) / "projects")
        project = self.store.create(title="replica", creative_mode="authorized_replication", audio_policy="silent")
        self.project_id = project["project_id"]
        self.source_receipt = {"project_id": self.project_id, "source_sha256": "a" * 64}
        self._persist_analysis_and_annotation()
        self.design_candidate = VideoRedesignService(self.store).prepare_candidate(
            self.project_id, "v001", self._payload()
        )
        self.binding = {
            "project_id": self.project_id, "source_sha256": "a" * 64,
            "creative_mode": "authorized_replication",
            "design_fingerprint": self.design_candidate["design_fingerprint"],
            "required_media": ["video"], "purpose": "campaign remake",
            "audience": "registered customers", "territory": "US",
        }
        self.valid_assertion = {
            "declarant": "rights-holder@example.test", "rights_basis": "written license",
            "evidence": [{"reference": "license-2026-09", "sha256": "c" * 64}],
            "allowed_media": ["video", "audio"],
            "allowed_reuse": ["likeness", "voice", "dialogue", "music"],
            "purpose": "campaign remake", "audience": "registered customers",
            "territory": "US", "expires_at": "2027-09-14T00:00:00Z",
        }
        self.confirmer = Confirmer()
        self.rights = VideoRightsService(self.store, self.confirmer, now=lambda: "2026-09-14T00:00:00Z")

    def _persist_analysis_and_annotation(self):
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
            "machine_fingerprint": "e" * 64,
        }
        self.store.write_version(self.project_id, "analysis", analysis, schema_name="shot_analysis.schema.json")
        annotation = {
            "schema_version": "1.0", "analysis_version": "v001", "machine_fingerprint": "e" * 64,
            "shots": [{"id": "S01", "shot_size": "wide", "category": "subject", "category_evidence": "subject person", "camera": "static", "frame_description": "A clearly described presenter standing inside a bright modern studio", "rhythm_role": None, "rhythm_evidence": None, "subjects": [], "on_screen_text": [], "dialogue": [], "narration": [], "music": [], "sound": [], "confidence": .9, "review_note": ""}], "transcript": None,
        }
        self.store.write_version(self.project_id, "annotation", annotation, schema_name="shot_annotation.schema.json")

    @staticmethod
    def _payload():
        return {
            "schema_version": "1.0", "creative_mode": "authorized_replication",
            "machine_fingerprint": "e" * 64, "preserve": ["likeness"],
            "required_media": ["video"], "purpose": "campaign remake",
            "audience": "registered customers", "territory": "US",
            "format": "short_video", "target_duration_seconds": 4.0, "aspect_ratio": "16:9",
            "platform": "web", "concept": "authorized campaign", "cast": ["presenter"],
            "settings": ["studio"], "palette": ["blue"], "visual_style": "editorial",
            "dialogue": [], "narration": [], "music": "licensed score", "sound_intent": "clean",
            "shots": [{"id": "S01", "prompt": "presenter in studio"}], "continuity": [], "author": "user",
        }

    def test_replication_requires_declarant_basis_scope_expiry_and_evidence(self):
        for missing in ("declarant", "rights_basis", "expires_at", "evidence", "allowed_media", "allowed_reuse", "purpose", "audience", "territory"):
            assertion = dict(self.valid_assertion); del assertion[missing]
            with self.subTest(missing=missing), self.assertRaises(ContractValidationError):
                self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, assertion)

    def test_rights_receipt_is_bound_to_source_project_mode_and_design(self):
        receipt = self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, self.valid_assertion)
        for key, value in (("project_id", "vp_" + "0" * 24), ("source_sha256", "d" * 64), ("creative_mode", "original_redesign"), ("design_fingerprint", "e" * 64)):
            binding = dict(self.binding); binding[key] = value
            with self.subTest(key=key), self.assertRaises(RightsScopeError):
                self.rights.assert_scope(receipt, required={"likeness"}, binding=binding)

    def test_assertion_rejects_source_receipt_that_differs_from_candidate(self):
        source = {**self.source_receipt, "source_sha256": "d" * 64}
        with self.assertRaises(ContractValidationError):
            self.rights.record_assertion(self.project_id, source, self.design_candidate, self.valid_assertion)

    def test_assertion_rejects_candidate_changed_after_fingerprinting(self):
        candidate = copy.deepcopy(self.design_candidate)
        candidate["payload"]["concept"] = "changed after fingerprinting"
        with self.assertRaises(ContractValidationError):
            self.rights.record_assertion(
                self.project_id, self.source_receipt, candidate, self.valid_assertion
            )

    def test_assertion_rejects_fabricated_self_fingerprinted_candidate_before_confirmation(self):
        core = {key: copy.deepcopy(value) for key, value in self.design_candidate.items() if key != "design_fingerprint"}
        core["payload"]["preserve"] = ["voice"]
        fabricated = {**core, "design_fingerprint": canonical_fingerprint(core)}
        with self.assertRaises(ContractValidationError):
            self.rights.record_assertion(
                self.project_id, self.source_receipt, fabricated, self.valid_assertion
            )
        self.assertEqual(self.confirmer.requests, [])
        self.assertFalse((self.store.project_root(self.project_id) / "rights_receipt").exists())

    def test_expired_or_narrower_audio_scope_fails_closed(self):
        expired = dict(self.valid_assertion); expired["expires_at"] = "2026-09-13T23:59:59Z"
        with self.assertRaises(ContractValidationError):
            self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, expired)
        narrow = dict(self.valid_assertion); narrow["allowed_reuse"] = ["dialogue"]
        narrow_receipt = self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, narrow)
        with self.assertRaises(RightsScopeError):
            self.rights.assert_scope(narrow_receipt, required={"dialogue", "voice", "music"}, binding=self.binding)

    def test_expiry_must_be_strict_rfc3339_and_later_than_assertion(self):
        for expires_at in (
            "2026-09-14T00:00:00Z",
            "2026-09-14 01:00:00+00:00",
            "2026-09-14T01:00:00",
        ):
            assertion = {**self.valid_assertion, "expires_at": expires_at}
            with self.subTest(expires_at=expires_at), self.assertRaises(ContractValidationError):
                self.rights.record_assertion(
                    self.project_id, self.source_receipt, self.design_candidate, assertion
                )
        self.assertEqual(self.confirmer.requests, [])

    def test_expiry_accepts_rfc3339_numeric_offset_and_compares_instants(self):
        assertion = {**self.valid_assertion, "expires_at": "2026-09-14T02:00:00+01:00"}
        receipt = self.rights.record_assertion(
            self.project_id, self.source_receipt, self.design_candidate, assertion
        )
        self.assertEqual(receipt["expires_at"], "2026-09-14T02:00:00+01:00")

    def test_mutating_confirmer_cannot_change_confirmed_or_persisted_bytes(self):
        rights = VideoRightsService(
            self.store, MutatingConfirmer(), now=lambda: "2026-09-14T00:00:00Z"
        )
        with self.assertRaises(RightsScopeError):
            rights.record_assertion(
                self.project_id, self.source_receipt, self.design_candidate,
                self.valid_assertion,
            )
        self.assertFalse((self.store.project_root(self.project_id) / "rights_receipt").exists())

    def test_narrower_media_and_context_scope_fails_closed(self):
        receipt = self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, self.valid_assertion)
        for changes in (
            {"required_media": ["video", "image"]},
            {"purpose": "different purpose"},
            {"audience": "public"},
            {"territory": "EU"},
        ):
            binding = {**self.binding, **changes}
            with self.subTest(changes=changes), self.assertRaises(RightsScopeError):
                self.rights.assert_scope(receipt, required={"likeness"}, binding=binding)

    def test_public_scope_requires_every_complete_typed_binding_field(self):
        receipt = self.rights.record_assertion(
            self.project_id, self.source_receipt, self.design_candidate, self.valid_assertion
        )
        complete = dict(self.binding)
        self.rights.assert_scope(receipt, required={"likeness"}, binding=complete)
        for missing in (
            "project_id", "source_sha256", "creative_mode", "design_fingerprint",
            "required_media", "purpose", "audience", "territory",
        ):
            binding = dict(complete); del binding[missing]
            with self.subTest(missing=missing), self.assertRaises(RightsScopeError):
                self.rights.assert_scope(receipt, required={"likeness"}, binding=binding)
        for invalid_media in ([], "video", [""], [1]):
            binding = {**complete, "required_media": invalid_media}
            with self.subTest(required_media=invalid_media), self.assertRaises(RightsScopeError):
                self.rights.assert_scope(receipt, required={"likeness"}, binding=binding)

    def test_indeterminate_retry_reconciles_after_expiry_without_side_effects(self):
        clock = ["2026-09-14T00:00:00Z"]
        rights = VideoRightsService(self.store, self.confirmer, now=lambda: clock[0])
        original = self.store._atomic_write
        captured = None

        def fail_after_publish(path, payload, *, indeterminate_error=None):
            nonlocal captured
            original(path, payload, indeterminate_error=None)
            captured = indeterminate_error
            raise indeterminate_error

        self.store._atomic_write = fail_after_publish
        with self.assertRaises(VersionCommitIndeterminateError):
            rights.record_assertion(
                self.project_id, self.source_receipt, self.design_candidate, self.valid_assertion
            )
        self.store._atomic_write = original
        clock[0] = "2028-01-01T00:00:00Z"
        receipt = rights.record_assertion(
            self.project_id, self.source_receipt, self.design_candidate,
            self.valid_assertion, indeterminate_commit=captured,
        )
        self.assertEqual(receipt["version"], "v001")
        self.assertEqual(len(self.confirmer.requests), 1)
        versions = self.store.project_root(self.project_id) / "rights_receipt"
        self.assertEqual([path.name for path in versions.glob("v*.json")], ["v001.json"])
        with self.assertRaises(RightsScopeError):
            rights.assert_scope(receipt, required={"likeness"}, binding={
                **self.binding, "required_media": ["video"],
                "purpose": "campaign remake", "audience": "registered customers",
                "territory": "US",
            })

    def test_native_confirmation_binds_assertion_and_candidate_before_persistence(self):
        receipt = self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, self.valid_assertion)
        self.assertEqual(self.confirmer.requests[0]["design_fingerprint"], self.design_candidate["design_fingerprint"])
        self.assertEqual(receipt["native_confirmation"], "native-video-rights-confirmed")
        self.assertEqual(receipt["evidence"], self.valid_assertion["evidence"])
        self.assertNotIn("verified", receipt)
        self.assertTrue((self.store.project_root(self.project_id) / "rights_receipt" / "v001.json").is_file())

    def test_boolean_or_unbound_rights_claim_is_rejected(self):
        with self.assertRaises(ContractValidationError):
            self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, {"authorized": True})

    def test_indeterminate_rights_commit_reconciles_without_allocating_v002(self):
        original = self.store._atomic_write
        captured = None

        def fail_after_publish(path, payload, *, indeterminate_error=None):
            nonlocal captured
            original(path, payload, indeterminate_error=None)
            captured = indeterminate_error
            raise indeterminate_error

        self.store._atomic_write = fail_after_publish
        with self.assertRaises(VersionCommitIndeterminateError):
            self.rights.record_assertion(
                self.project_id, self.source_receipt, self.design_candidate, self.valid_assertion
            )
        self.assertEqual(len(self.confirmer.requests), 1)
        self.store._atomic_write = original
        receipt = self.rights.record_assertion(
            self.project_id, self.source_receipt, self.design_candidate,
            self.valid_assertion, indeterminate_commit=captured,
        )
        self.assertEqual(receipt["version"], "v001")
        self.assertEqual(len(self.confirmer.requests), 1)
        versions = self.store.project_root(self.project_id) / "rights_receipt"
        self.assertEqual([path.name for path in versions.glob("v*.json")], ["v001.json"])

    def test_indeterminate_rights_retry_rejects_changed_assertion_and_metadata(self):
        original = self.store._atomic_write
        captured = None

        def fail_after_publish(path, payload, *, indeterminate_error=None):
            nonlocal captured
            original(path, payload, indeterminate_error=None)
            captured = indeterminate_error
            raise indeterminate_error

        self.store._atomic_write = fail_after_publish
        with self.assertRaises(VersionCommitIndeterminateError):
            self.rights.record_assertion(
                self.project_id, self.source_receipt, self.design_candidate, self.valid_assertion
            )
        self.store._atomic_write = original

        changed = {**self.valid_assertion, "purpose": "different purpose"}
        with self.assertRaises(VersionReconciliationError):
            self.rights.record_assertion(
                self.project_id, self.source_receipt, self.design_candidate, changed,
                indeterminate_commit=captured,
            )
        wrong = VersionCommitIndeterminateError(
            project_id=self.project_id, family="redesign", version=captured.version,
            path=captured.path, payload_fingerprint=captured.payload_fingerprint,
        )
        with self.assertRaises(VersionReconciliationError):
            self.rights.record_assertion(
                self.project_id, self.source_receipt, self.design_candidate,
                self.valid_assertion, indeterminate_commit=wrong,
            )
        self.assertEqual(len(self.confirmer.requests), 1)
        versions = self.store.project_root(self.project_id) / "rights_receipt"
        self.assertEqual([path.name for path in versions.glob("v*.json")], ["v001.json"])


if __name__ == "__main__":
    unittest.main()
