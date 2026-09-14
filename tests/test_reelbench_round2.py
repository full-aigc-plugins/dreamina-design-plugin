"""Regression cases for the production action boundary and immutable lineage."""
import json
import os
import hashlib
import shutil
import subprocess
import threading
import concurrent.futures
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from tests import test_reelbench_project_service as fixtures
from scripts.bounded_process import BoundedProcessResult
from scripts.video_project_store import VersionCommitIndeterminateError
from scripts.json_contracts import canonical_fingerprint
from scripts.trusted_media_tools import TrustedMediaToolStore
from scripts import reelbench_workspace as ws
from scripts.reelbench_adapter import ReelBenchAdapterError


fixture_path = fixtures.fixture_path


class ReelBenchRoundTwoTests(unittest.TestCase):
    setUp = fixtures.ReelBenchProjectServiceTests.setUp
    _service = fixtures.ReelBenchProjectServiceTests._service
    def action(self, action, parent=None, **options):
        return self.service.run(self.project_id, action=action, expected_parent=parent,
                                source_receipt_version=self.source_receipt['version'], **options)

    def test_all_evidence_commands_are_bound_in_order(self):
        seed = self.action('seed')
        result = self.action('evidence', seed['version'])
        self.assertEqual([item['action'] for item in result['commands']], ['frames', 'sheet', 'sheet'])
        self.assertEqual(result['parent_fingerprint'], seed['evidence_fingerprint'])
        self.assertTrue(any(item['path'].endswith('/shots.json') for item in result['consumed_artifacts']))

    def test_validate_rejects_tampered_grandparent_before_any_child(self):
        seed = self.action('seed')
        evidence = self.action('evidence', seed['version'])
        path = self.store.project_root(self.project_id) / seed['artifacts'][0]['path']
        path.write_bytes(b'{"meta":{"durationSeconds":1},"shots":[]}')
        with patch.object(self.service._adapter, 'validate', side_effect=AssertionError('child was launched')):
            with self.assertRaisesRegex(ValueError, 'digest|artifact'):
                self.action('validate', evidence['version'])

    def test_new_seed_cannot_reuse_old_seed_frame_lineage(self):
        seed = self.action('seed')
        evidence = self.action('evidence', seed['version'])
        with self.assertRaisesRegex(ValueError, 'seed'):
            self.action('seed', evidence['version'])

    def test_missing_tail_frame_rejects_partial_evidence(self):
        seed = self.action('seed')
        original = self.service._adapter.frames
        def frames(**kwargs):
            result = original(**kwargs)
            (fixture_path(kwargs['frames_dir']) / 'S01b.jpg').unlink()
            return result
        with patch.object(self.service._adapter, 'frames', side_effect=frames):
            with self.assertRaisesRegex(ValueError, 'complete|inventory'):
                self.action('evidence', seed['version'])

    def test_generic_error_after_visible_commit_is_typed_and_preserves_artifacts(self):
        write = self.store.write_version
        def fail_after_commit(*args, **kwargs):
            write(*args, **kwargs)
            raise RuntimeError('post-publication callback failed')
        with patch.object(self.store, 'write_version', side_effect=fail_after_commit):
            with self.assertRaises(VersionCommitIndeterminateError) as caught:
                self.action('seed')
        recovered = self.service.reconcile_indeterminate(caught.exception)
        self.assertTrue(all((self.store.project_root(self.project_id) / a['path']).exists() for a in recovered['artifacts']))

    def test_two_services_serialize_on_directory_even_if_lockfile_is_replaced(self):
        entered = threading.Event()
        release = threading.Event()
        runner = self.service._adapter._runner
        other = self._service()
        def blocked(argv, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test release timeout')
            return runner(argv, **kwargs)
        self.service._adapter._runner = blocked
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.action, 'seed')
            self.assertTrue(entered.wait(5))
            lock = self.store.project_root(self.project_id) / '.reelbench.operation.lock'
            lock.write_text('a replacement lock file has no authority')
            second = pool.submit(other.run, self.project_id, action='seed', expected_parent=None,
                                 source_receipt_version=self.source_receipt['version'])
            release.set()
            self.assertEqual(first.result(5)['version'], 'v001')
            with self.assertRaisesRegex(ValueError, 'expected parent'):
                second.result(5)

    def test_short_write_is_completed_and_zero_write_fails_without_receipt(self):
        write = os.write
        def short(fd, data):
            return write(fd, data[:max(1, len(data) // 2)])
        with patch('os.write', side_effect=short):
            self.assertEqual(self.action('seed')['version'], 'v001')
        with patch('os.write', return_value=0):
            with self.assertRaisesRegex(OSError, 'short write'):
                self.action('evidence', 'v001')
        self.assertFalse((self.store.project_root(self.project_id) / 'reelbench_evidence/v002.json').exists())

    def test_project_swap_and_restore_does_not_redirect_source_or_outputs(self):
        runner = self.service._adapter._runner
        original = self.store.project_root(self.project_id)
        moved = original.with_name(original.name + '-moved')
        def swapped(argv, **kwargs):
            original.rename(moved)
            original.mkdir(mode=0o700)
            try:
                result = runner(argv, **kwargs)
                self.assertEqual(list(original.iterdir()), [])
                return result
            finally:
                original.rmdir()
                moved.rename(original)
        self.service._adapter._runner = swapped
        self.assertEqual(self.action('seed')['version'], 'v001')

    def test_ancestor_symlink_is_rejected_before_launch(self):
        seed = self.action('seed')
        original = self.store.project_root(self.project_id) / 'reelbench/v001'
        moved = original.with_name('original')
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        with patch.object(self.service._adapter, 'frames', side_effect=AssertionError('child launched')):
            with self.assertRaises((OSError, ValueError)):
                self.action('evidence', seed['version'])

    def test_source_receipt_filename_and_embedded_version_must_match(self):
        path = self.store.project_root(self.project_id) / 'source_receipt/v001.json'
        document = json.loads(path.read_text())
        document['version'] = 'v999'
        path.write_text(json.dumps(document))
        with patch.object(self.service._adapter, 'seed', side_effect=AssertionError('child launched')):
            with self.assertRaisesRegex(ValueError, 'version'):
                self.action('seed')

    def test_recovery_refuses_a_visible_receipt_with_missing_artifact(self):
        seed = self.action('seed')
        error = VersionCommitIndeterminateError(project_id=self.project_id, family='reelbench_evidence',
            version=seed['version'], path=self.store.project_root(self.project_id) / 'reelbench_evidence/v001.json',
            payload_fingerprint=canonical_fingerprint(seed))
        (self.store.project_root(self.project_id) / seed['artifacts'][0]['path']).unlink()
        with self.assertRaisesRegex(ValueError, 'artifact'):
            self.service.reconcile_indeterminate(error)

    def test_cleanup_never_deletes_a_replacement_workspace(self):
        runner = self.service._adapter._runner
        replacement = []
        def replace_after_child(argv, **kwargs):
            result = runner(argv, **kwargs)
            work = fixture_path(f"/dev/fd/{argv[4]}/output/track.json").parents[1]
            moved = work.with_name(work.name + '-moved')
            work.rename(moved)
            work.mkdir(mode=0o700)
            (work / 'foreign.txt').write_text('must survive cleanup')
            replacement.append(work)
            return result
        self.service._adapter._runner = replace_after_child
        with self.assertRaises(VersionCommitIndeterminateError):
            self.action('seed')
        self.assertEqual((replacement[0] / 'foreign.txt').read_text(), 'must survive cleanup')

    def test_output_directory_replacement_cannot_redirect_copies_or_cleanup(self):
        original_copy = ws.copy
        root = self.store.project_root(self.project_id)
        changed = []
        def copying(source, name, destination, target, **kwargs):
            if target == 'shots.json' and not changed:
                directory = root / 'reelbench/v001'
                directory.rename(directory.with_name('original-v001'))
                directory.mkdir(mode=0o700)
                (directory / 'foreign.txt').write_text('preserve')
                changed.append(directory)
            return original_copy(source, name, destination, target, **kwargs)
        with patch.object(ws, 'copy', side_effect=copying):
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                self.action('seed')
        self.assertEqual([p.name for p in changed[0].iterdir()], ['foreign.txt'])
        self.assertFalse((root / 'reelbench_evidence/v001.json').exists())

    def test_workspace_setup_failure_does_not_leak_descriptors(self):
        opened = set()
        real_open, real_close = os.open, os.close
        def opening(path, flags, *args, **kwargs):
            if str(path) == 'source':
                raise OSError('injected setup failure')
            descriptor = real_open(path, flags, *args, **kwargs)
            opened.add(descriptor)
            return descriptor
        def closing(fd):
            opened.discard(fd)
            return real_close(fd)
        with patch('os.open', side_effect=opening), patch('os.close', side_effect=closing):
            with self.assertRaises(OSError):
                self.action('seed')
        self.assertEqual(opened, set())
        self.assertFalse(any(p.name.startswith('.reelbench-work-') for p in self.store.project_root(self.project_id).iterdir()))

    def test_more_than_120_shots_is_rejected_before_frame_process(self):
        seed = self.action('seed')
        path = self.store.project_root(self.project_id) / next(a['path'] for a in seed['artifacts'] if a['path'].endswith('shots.json'))
        document = json.loads(path.read_text())
        document['shots'] = [{'id': f'S{i:02d}', 'start': 0, 'end': 8, 'seconds': 8} for i in range(1, 122)]
        path.write_text(json.dumps(document))
        for artifact in seed['artifacts']:
            if artifact['path'].endswith('shots.json'):
                artifact['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                artifact['size_bytes'] = path.stat().st_size
        seed['evidence_fingerprint'] = canonical_fingerprint({k: v for k, v in seed.items() if k != 'evidence_fingerprint'})
        (self.store.project_root(self.project_id) / 'reelbench_evidence/v001.json').write_text(json.dumps(seed))
        with patch.object(self.service._adapter, 'frames', side_effect=AssertionError('child launched')):
            with self.assertRaises(ValueError):
                self.action('evidence', seed['version'])

    def test_persistent_temp_unlink_cannot_hide_visible_receipt(self):
        real_unlink = os.unlink
        def unlink(path, *args, **kwargs):
            if str(path).startswith('.v001.json.') or Path(path).name.startswith('.v001.json.'):
                raise PermissionError('persistent temp unlink failure')
            return real_unlink(path, *args, **kwargs)
        with patch('scripts.video_project_store.os.unlink', side_effect=unlink):
            with self.assertRaises(VersionCommitIndeterminateError) as caught:
                self.action('seed')
        receipt = self.service.reconcile_indeterminate(caught.exception)
        self.assertEqual(receipt['version'], 'v001')
        self.assertTrue(all((self.store.project_root(self.project_id) / item['path']).is_file() for item in receipt['artifacts']))

    def test_trusted_production_chain_runs_all_commands_and_offline_html(self):
        self._trusted_chain()

    def test_trusted_launch_rejects_replaced_same_byte_node(self):
        def tamper(work):
            target = work / 'tools/node'
            replacement = work / 'tools/other-node'
            shutil.copyfile(target, replacement)
            replacement.chmod(0o500)
            replacement.replace(target)
        self._before_launch = tamper
        with self.assertRaises(ReelBenchAdapterError):
            self._trusted_chain()
        self.assertFalse((self.store.project_root(self.project_id) / 'reelbench_evidence/v001.json').exists())

    def test_trusted_launch_rejects_copied_source_tampering(self):
        def tamper(work):
            target = next((work / 'source').iterdir())
            target.chmod(0o600)
            content = target.read_bytes()
            target.write_bytes(b'x' + content[1:])
            target.chmod(0o400)
        self._before_launch = tamper
        with self.assertRaises(ReelBenchAdapterError):
            self._trusted_chain()

    def test_trusted_launch_rejects_pinned_report_asset_tampering(self):
        def tamper(work):
            target = work / 'script/report.js'
            target.chmod(0o600)
            target.write_bytes(b'evil')
        self._before_launch = tamper
        with self.assertRaises(ReelBenchAdapterError):
            self._trusted_chain()

    def _trusted_chain(self):
        paths = {kind: shutil.which(kind) for kind in ('node', 'ffmpeg', 'ffprobe')}
        if not all(paths.values()):
            self.skipTest('local Node/FFmpeg prerequisites unavailable')
        self.source.chmod(0o600)
        subprocess.run([paths['ffmpeg'], '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'color=c=red:s=640x360:r=25:d=8', '-c:v', 'libx264', str(self.source)],
                       check=True, timeout=30, capture_output=True)
        self.source.chmod(0o400)
        self.source_receipt['source_sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.source_receipt['size_bytes'] = self.source.stat().st_size
        source_receipt_path = self.store.project_root(self.project_id) / 'source_receipt/v001.json'
        source_receipt_path.write_text(json.dumps(self.source_receipt))
        base = Path(self.temp.name).resolve()
        trust = TrustedMediaToolStore(base / 'trusted.json', base / 'staged')
        class Approval:
            def confirm_media_tool_enrollment(self, **kwargs):
                return None
        for kind, path in paths.items():
            enrolled = base / kind
            shutil.copyfile(Path(path).resolve(), enrolled)
            enrolled.chmod(0o500)
            trust.enroll(kind, enrolled, Approval())
        self.service._adapter._tool_store = trust
        self.service._adapter._tools = {kind: trust.resolve_verified(kind) for kind in paths}
        from scripts.bounded_process import run_bounded
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(argv[:3], ['/usr/bin/python3', '-I', '-c'])
            self.assertEqual(argv[6:8], ['tools/node', 'script/video-shots.mjs'])
            self.assertEqual(kwargs['env']['PATH'], 'tools/bin')
            self.assertTrue(all(not value.startswith('/') for value in argv[6:]))
            workspace = fixture_path(f"/dev/fd/{argv[4]}/sentinel").parent
            if getattr(self, '_before_launch', None) is not None:
                self._before_launch(workspace)
            moved = workspace.with_name(workspace.name + '-pinned')
            source_dir = self.source.parent
            source_moved = source_dir.with_name('source-pinned')
            workspace.rename(moved)
            workspace.mkdir(mode=0o700)
            source_dir.rename(source_moved)
            source_dir.mkdir(mode=0o700)
            (source_dir / self.source.name).write_bytes(b'untrusted pathname replacement')
            try:
                result = run_bounded(argv, **kwargs)
                self.assertEqual(list(workspace.iterdir()), [])
                return result
            finally:
                (source_dir / self.source.name).unlink()
                source_dir.rmdir()
                source_moved.rename(source_dir)
                workspace.rmdir()
                moved.rename(workspace)
        self.service._adapter._runner = runner
        seed = self.action('seed')
        # Install a human-annotated fixture as the seed receipt before running the
        # real upstream gates; the machine draft is deliberately not a gate pass.
        shot_path = self.store.project_root(self.project_id) / next(a['path'] for a in seed['artifacts'] if a['path'].endswith('/shots.json'))
        doc = json.loads(shot_path.read_text())
        doc['shots'][0].update(size='none', category='empty', camera='static',
            frame='A red rectangular field fills the full screen with no visible text or moving subjects.')
        shot_path.write_text(json.dumps(doc))
        for artifact in seed['artifacts']:
            if artifact['path'].endswith('/shots.json'):
                artifact['sha256'] = hashlib.sha256(shot_path.read_bytes()).hexdigest()
                artifact['size_bytes'] = shot_path.stat().st_size
                seed['shots']['sha256'] = artifact['sha256']
        seed['evidence_fingerprint'] = canonical_fingerprint({k: v for k, v in seed.items() if k != 'evidence_fingerprint'})
        (self.store.project_root(self.project_id) / 'reelbench_evidence/v001.json').write_text(json.dumps(seed))
        evidence = self.action('evidence', seed['version'])
        validated = self.action('validate', evidence['version'])
        self.assertFalse(any(g['status'] == 'FAIL' for g in validated['gates']), validated['gates'])
        rendered = self.action('render', validated['version'], mode='html')
        self.assertEqual(len(calls), 6)
        report = self.store.project_root(self.project_id) / next(a['path'] for a in rendered['artifacts'] if a['path'].endswith('report.html'))
        self.assertIn('S01', report.read_text())
        self.assertTrue((report.parent / 'inputs/frames/S01a.jpg').is_file())
        class Media(HTMLParser):
            source = None
            def handle_starttag(self, tag, attrs):
                if tag == 'video':
                    self.source = dict(attrs).get('src')
        media = Media()
        media.feed(report.read_text())
        self.assertEqual((report.parent / media.source).resolve(), self.source.resolve())
        self.assertEqual(list((base / 'staged').iterdir()), [])
