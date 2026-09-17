"""Local synthetic media through production staging, browser lease and probes."""
import unittest
import os
import hashlib
import json
from pathlib import Path
from tests.reelbench_sync_fixture import SyncFixture
from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_sync_service import ReelBenchSyncError

@unittest.skipUnless(os.environ.get('REELBENCH_REAL_SYNC') == '1' and Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').is_file(), 'opt-in real trusted sync prerequisite')
class SyncRuntimeTests(unittest.TestCase):
    def test_silent_export_is_measured_and_immutable(self):
        f = SyncFixture(self, browser=True, audio=True, cuts=True)
        receipt = f.service.export(*f.args, audio_policy='silent')
        self.assertEqual(receipt['artifact_role'], 'synchronized_review')
        self.assertEqual(set(receipt['gates']), {'duration','dimensions','codec','audio_policy','cut_alignment','sampled_correspondence'})
        self.assertTrue(all(g['passed'] for g in receipt['gates'].values()))
        self.assertEqual(receipt['gates']['audio_policy']['measured'], 0)
        self.assertEqual([s['shot_id'] for s in receipt['sampled_alignment']],['S01','S02'])
        self.assertEqual(receipt['sampled_alignment'][1]['cut_seconds'],4)
        verified = f.service.verify(*f.args, sync_version=receipt['version'])
        self.assertEqual(verified['evidence_fingerprint'], receipt['evidence_fingerprint'])
        # Recompute the caller-visible receipt fingerprint after replacing the
        # review by source bytes. Only actual probing can reject this forgery.
        root=f.project.store.project_root(f.args[0])
        artifact=next(a for a in receipt['artifacts'] if a['path'].endswith('/review.mp4'))
        target=root/artifact['path']
        target.chmod(0o600)
        target.write_bytes(f.project.source.read_bytes())
        artifact.update(size_bytes=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        receipt['evidence_fingerprint']=canonical_fingerprint({k:v for k,v in receipt.items() if k!='evidence_fingerprint'})
        f.project.store._atomic_write(root/'reelbench_sync'/f"{receipt['version']}.json",receipt)
        with self.assertRaisesRegex(ReelBenchSyncError,'media gate failed'):
            f.service.verify(*f.args,sync_version=receipt['version'])

    def test_preserved_audio_packet_bytes_equal_source(self):
        f = SyncFixture(self, browser=True, audio=True)
        project = f.project.store.get(f.args[0])
        project['audio_policy'] = 'preserve_authorized_audio'
        f.project.store._atomic_write(f.project.store.project_root(f.args[0]) / 'project.json', project)
        receipt = f.service.export(*f.args, audio_policy='preserve_source_audio')
        measured = receipt['gates']['audio_policy']['measured']
        self.assertEqual(measured['source_packets'], measured['output_packets'])
        self.assertIn('-t', receipt['composition_argv']['actual_argv'])
        self.assertTrue(any('-c:a' in c['argv'] and 'copy' in c['argv'] for c in receipt['commands']))
