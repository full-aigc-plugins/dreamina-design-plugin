from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.json_contracts import canonical_fingerprint
from scripts.trusted_capability_provider import TrustedCapabilityProvider
from scripts.trusted_cli import TrustedCliError, TrustedCliStore
from scripts.reference_policy import ReferencePolicy
from scripts.video_generation_planner import VideoGenerationPlanner
from scripts.video_project_store import VideoProjectStore
from scripts.video_redesign_service import VideoRedesignService
from scripts.video_rights_service import VideoRightsService


class _Approve:
    def confirm_cli_enrollment(self, **kwargs):
        return None


def _cli(path: Path, schema: str | None = None) -> Path:
    payload = schema or json.dumps({
        "modes": ["text2video"],
        "models": [{"name": "seedance", "modes": ["text2video"], "resolutions": ["720p"], "ratios": ["16:9"], "duration_min_seconds": 4, "duration_max_seconds": 8}],
        "resolutions": {"video": ["720p"]}, "ratios": ["16:9"],
    })
    path.write_text(f'''#!/bin/sh
case "$1" in
  --version) echo '{{"version":"1.4.18","commit":"abcdef0"}}' ;;
  schema) cat <<'EOF'
{payload}
EOF
  ;;
esac
''')
    path.chmod(0o500)
    return path


class TrustedCapabilityProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cli = _cli(root / "dreamina")
        self.store = TrustedCliStore(root / "config" / "trusted-cli.json")
        self.store.enroll(self.cli, approval_provider=_Approve())

    def test_capture_binds_closed_inode_and_snapshot_receipt(self):
        evidence = TrustedCapabilityProvider(self.store).capture()
        self.assertEqual(set(evidence), {"snapshot", "identity_receipt"})
        receipt = evidence["identity_receipt"]
        self.assertEqual(set(receipt), {"cli_path", "cli_sha256", "device", "inode", "size_bytes", "mode", "owner_uid", "cli_version", "cli_commit", "captured_at", "snapshot_fingerprint"})
        self.assertEqual(receipt["snapshot_fingerprint"], canonical_fingerprint(evidence["snapshot"]))
        self.assertEqual(receipt["mode"], 0o500)

    def test_path_swap_during_adapter_capture_fails_closed_and_adapter_closes(self):
        from scripts.dreamina_adapter import DreaminaAdapter as RealAdapter
        closed = []

        class SwappingAdapter(RealAdapter):
            def capability_snapshot(inner_self):
                snapshot = super().capability_snapshot()
                replacement = self.cli.with_suffix(".replacement")
                _cli(replacement)
                os.replace(replacement, self.cli)
                return snapshot

            def close(inner_self):
                closed.append(True)
                super().close()

        with patch("scripts.trusted_capability_provider.DreaminaAdapter", SwappingAdapter):
            with self.assertRaisesRegex(TrustedCliError, "path changed"):
                TrustedCapabilityProvider(self.store).capture()
        self.assertEqual(closed, [True])

    def test_noncanonical_or_schema_invalid_snapshot_fails_closed(self):
        invalid = _cli(Path(self.tmp.name) / "invalid", '{"modes":["invented"]}')
        store = TrustedCliStore(Path(self.tmp.name) / "bad-config" / "trusted-cli.json")
        store.enroll(invalid, approval_provider=_Approve())
        with self.assertRaises(Exception):
            TrustedCapabilityProvider(store).capture()

    def test_digest_or_permission_drift_fails_closed(self):
        self.cli.chmod(0o700)
        self.cli.write_text("#!/bin/sh\necho changed\n")
        self.cli.chmod(0o500)
        with self.assertRaisesRegex(TrustedCliError, "digest"):
            TrustedCapabilityProvider(self.store).capture()

    def test_public_planner_end_to_end_uses_persisted_rights_capabilities_and_durable_reference(self):
        root = Path(self.tmp.name)
        project_store = VideoProjectStore(root / "projects")
        project = project_store.create(title="replica", creative_mode="authorized_replication", audio_policy="silent")
        project_id = project["project_id"]
        machine = "b" * 64
        source_receipt = {
            "schema_version":"1.0", "version":"v001", "project_id":project_id,
            "source_sha256":"a"*64, "size_bytes":12, "mime_type":"video/mp4",
            "video_codec":"h264", "width":1280, "height":720, "fps":24.0,
            "duration_seconds":4.0, "audio_streams":[], "approved_roots_digest":"c"*64,
            "staged_path":"/private/source.mp4", "intake_at":"2026-09-14T00:00:00Z",
        }
        analysis = {
            "schema_version":"1.0", "analysis_id":"an_"+"d"*24, "project_id":project_id,
            "source":source_receipt, "parameters":{"scene_threshold":.3,"min_shot_seconds":.3,"track_hz":5},
            "cuts":[0.0,4.0], "manual_cuts":[],
            "shots":[{"id":"S01","measured":{"start_seconds":0.0,"end_seconds":4.0,"duration_seconds":4.0,"motion_median":0.0,"boundary_source":"source_start"},"semantic":None}],
            "track_path":"/private/track.json", "frame_checksums":{label:{"sha256":"e"*64,"path":"/private/frame.png","at_seconds":at,"frame_width":480,"boundary_fingerprint":"f"*64} for label,at in (("S01:a",.5),("S01:b",3.5))},
            "machine_fingerprint":machine,
        }
        project_store.write_version(project_id,"analysis",analysis,schema_name="shot_analysis.schema.json")
        annotation = {"schema_version":"1.0","analysis_version":"v001","machine_fingerprint":machine,"shots":[{"id":"S01","shot_size":"wide","category":"subject","category_evidence":"subject person","camera":"static","frame_description":"A licensed presenter in a bright modern studio","rhythm_role":None,"rhythm_evidence":None,"subjects":[],"on_screen_text":[],"dialogue":[],"narration":[],"music":[],"sound":[],"confidence":.9,"review_note":""}],"transcript":None}
        project_store.write_version(project_id,"annotation",annotation,schema_name="shot_annotation.schema.json")
        payload = {"schema_version":"1.0","creative_mode":"authorized_replication","machine_fingerprint":machine,"preserve":["likeness"],"required_media":["video","image"],"purpose":"public commercial campaign","audience":"public","territory":"worldwide","format":"short_video","target_duration_seconds":4.0,"aspect_ratio":"16:9","platform":"web","concept":"Licensed launch story","cast":["presenter"],"settings":["studio"],"palette":["blue"],"visual_style":"clean editorial","dialogue":[],"narration":[],"music":"licensed score","sound_intent":"silent","shots":[{"id":"S01","prompt":"晨雾中的授权主持人"}],"continuity":["identity"],"author":"user"}
        redesign = VideoRedesignService(project_store)
        candidate = redesign.prepare_candidate(project_id,"v001",payload)
        class Confirmer:
            def confirm_video_rights(self, request): return "native-video-rights-confirmed"
        rights = VideoRightsService(project_store,Confirmer())
        receipt = rights.record_assertion(project_id,{"project_id":project_id,"source_sha256":"a"*64},candidate,{"declarant":"holder@example.test","rights_basis":"written license","evidence":[{"reference":"license","sha256":"c"*64}],"allowed_media":["video","image"],"allowed_reuse":["likeness"],"purpose":"public commercial campaign","audience":"public","territory":"worldwide","expires_at":"2099-01-01T00:00:00Z"})
        design = redesign.commit_version(candidate,receipt["receipt_id"])

        image = root / "approved" / "subject.png"
        image.parent.mkdir()
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"z"*64)
        policy = ReferencePolicy(approved_roots=[image.parent], durable_root=project_store.project_root(project_id)/"quote_references")
        schema = json.dumps({"modes":["image2video"],"models":[{"name":"seedance","modes":["image2video"],"resolutions":["720p"],"ratios":["16:9"],"duration_min_seconds":4,"duration_max_seconds":8,"max_references":1}],"resolutions":{"video":["720p"]},"ratios":["16:9"]})
        cli = _cli(root/"planner-dreamina",schema)
        cli_store = TrustedCliStore(root/"planner-config"/"trusted-cli.json")
        cli_store.enroll(cli,approval_provider=_Approve())
        planner = VideoGenerationPlanner(reference_policy=policy,project_store=project_store,capability_provider_factory=lambda: TrustedCapabilityProvider(cli_store))
        quote = planner.plan(project_id,design["version"],{"kind":"operator_ceiling","credit_ceiling":7,"currency":"credits","source":"operator:test","recorded_at":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()},generation={"S01":{"model":"seedance","video_resolution":"720p","ratio":"16:9","duration_seconds":4,"references":[{"path":str(image),"role":"subject"}],"max_attempts":2,"repair_directives":["identity_consistency"]}},output_destination="/exports/final.mp4",output_profile={"container":"mp4","codec":"h264"})
        self.assertEqual((quote["item_count"],quote["task_count"],quote["reserved_retry_count"],quote["target_total_duration_seconds"],quote["total_credit_ceiling"]),(1,2,1,4,14))
        durable = Path(quote["items"][0]["attempts"][0]["request"]["references"][0]["path"])
        self.assertTrue(durable.is_file())
        self.assertEqual(durable.stat().st_mode & 0o777,0o400)
        fingerprints = quote["items"][0]["request_fingerprints"]
        self.assertEqual(len(set(fingerprints)),2)
        image.unlink()
        policy.close()
        self.assertTrue(durable.is_file())
        planner.validate_quote(quote)
        self.cli.chmod(0o522)
        with self.assertRaisesRegex(TrustedCliError, "unsafe"):
            TrustedCapabilityProvider(self.store).capture()


if __name__ == "__main__":
    unittest.main()
