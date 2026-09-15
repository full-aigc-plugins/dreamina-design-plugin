"""Exercise the project-only sync boundary with real trusted Node and ffprobe."""
import unittest
from scripts.reelbench_sync_service import ReelBenchSyncError, ReelBenchSyncBlockedError, FinalMediaVerificationError
from tests.reelbench_sync_fixture import SyncFixture

class ReelBenchSyncServiceTests(unittest.TestCase):
    def setUp(self): self.f = SyncFixture(self)

    def test_plan_is_real_upstream_geometry_and_leaves_no_version(self):
        result = self.f.service.plan(*self.f.args)
        self.assertEqual(result['video'], {'width':640,'height':360})
        self.assertEqual(result['panel'], {'width':640,'height':288})
        self.assertEqual(result['output'], {'width':640,'height':648})
        self.assertEqual(result['fps'], 30)
        self.assertIsNone(self.f.service.status(self.f.args[0])['latest_version'])
        self.assertEqual(list(self.f.project.store.project_root(self.f.args[0]).glob('.reelbench-sync-*')), [])

    def test_missing_browser_is_typed_and_no_workspace_is_created(self):
        with self.assertRaises(ReelBenchSyncBlockedError): self.f.service.panels(*self.f.args)
        self.assertIsNone(self.f.service.status(self.f.args[0])['latest_version'])

    def test_source_corruption_is_rejected_before_plan(self):
        self.f.project.source.chmod(0o600)
        self.f.project.source.write_bytes(b'foreign')
        with self.assertRaises(ValueError): self.f.service.plan(*self.f.args)

    def test_review_role_is_rejected(self):
        with self.assertRaises(FinalMediaVerificationError): self.f.service.accept_generated_shot({'artifact_role':'synchronized_review'})

    def test_preserve_requires_approved_policy(self):
        with self.assertRaisesRegex(ReelBenchSyncError, 'approved'):
            self.f.service.export(*self.f.args, audio_policy='preserve_source_audio')
