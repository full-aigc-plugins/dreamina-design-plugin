from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.json_contracts import ContractValidationError
from scripts.video_project_store import VideoProjectStore
from scripts.video_rights_service import RightsScopeError, VideoRightsService


class Confirmer:
    def __init__(self) -> None: self.requests = []
    def confirm_video_rights(self, request):
        self.requests.append(copy.deepcopy(request))
        return "native-video-rights-confirmed"


class VideoRightsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = VideoProjectStore(Path(self.temp.name) / "projects")
        project = self.store.create(title="replica", creative_mode="authorized_replication", audio_policy="silent")
        self.project_id = project["project_id"]
        self.source_receipt = {"project_id": self.project_id, "source_sha256": "a" * 64}
        self.design_candidate = {"project_id": self.project_id, "source_sha256": "a" * 64, "creative_mode": "authorized_replication", "design_fingerprint": "b" * 64}
        self.binding = {"project_id": self.project_id, "source_sha256": "a" * 64, "creative_mode": "authorized_replication", "design_fingerprint": "b" * 64}
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

    def test_expired_or_narrower_audio_scope_fails_closed(self):
        expired = dict(self.valid_assertion); expired["expires_at"] = "2026-09-13T23:59:59Z"
        expired_receipt = self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, expired)
        with self.assertRaises(RightsScopeError):
            self.rights.assert_scope(expired_receipt, required={"dialogue"}, binding=self.binding)
        narrow = dict(self.valid_assertion); narrow["allowed_reuse"] = ["dialogue"]
        narrow_receipt = self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, narrow)
        with self.assertRaises(RightsScopeError):
            self.rights.assert_scope(narrow_receipt, required={"dialogue", "voice", "music"}, binding=self.binding)

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

    def test_native_confirmation_binds_assertion_and_candidate_before_persistence(self):
        receipt = self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, self.valid_assertion)
        self.assertEqual(self.confirmer.requests[0]["design_fingerprint"], "b" * 64)
        self.assertEqual(receipt["native_confirmation"], "native-video-rights-confirmed")
        self.assertEqual(receipt["evidence"], self.valid_assertion["evidence"])
        self.assertNotIn("verified", receipt)
        self.assertTrue((self.store.project_root(self.project_id) / "rights_receipt" / "v001.json").is_file())

    def test_boolean_or_unbound_rights_claim_is_rejected(self):
        with self.assertRaises(ContractValidationError):
            self.rights.record_assertion(self.project_id, self.source_receipt, self.design_candidate, {"authorized": True})


if __name__ == "__main__":
    unittest.main()
