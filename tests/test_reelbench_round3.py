"""Round-three regressions at live process, project entry, and recovery boundaries."""
import json
import os
import sys
import tempfile
import unittest
import subprocess
import fcntl
import threading
import concurrent.futures
import shutil
from pathlib import Path
from unittest.mock import patch

from scripts import reelbench_workspace as ws
from scripts.bounded_process import run_bounded
from tests import test_reelbench_project_service as fixtures
from tests.test_reelbench_contracts import evidence
from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_contracts import validate_reelbench_evidence
from scripts.video_project_store import VersionCommitIndeterminateError


class ReelBenchRoundThreeTests(unittest.TestCase):
    setUp = fixtures.ReelBenchProjectServiceTests.setUp
    _service = fixtures.ReelBenchProjectServiceTests._service

    def action(self, action='seed', parent=None):
        return self.service.run(self.project_id, action=action, expected_parent=parent,
            source_receipt_version=self.source_receipt['version'])

    def test_permanent_project_replacement_cannot_publish_detached_receipt(self):
        runner = self.service._adapter._runner
        root = self.store.project_root(self.project_id)
        moved = root.with_name(root.name + '-detached')
        def replacing(argv, **kwargs):
            result = runner(argv, **kwargs)
            root.rename(moved)
            root.mkdir(mode=0o700)
            return result
        self.service._adapter._runner = replacing
        with self.assertRaisesRegex(ValueError, 'project.*identity'):
            self.action()
        self.assertFalse((moved / 'reelbench_evidence/v001.json').exists())
        self.assertFalse((root / 'reelbench_evidence/v001.json').exists())

    def test_live_monitor_kills_many_subcap_files_before_success_marker(self):
        root = Path(self.temp.name) / 'quota'
        root.mkdir()
        fd = os.open(root, ws.DIRECTORY)
        self.addCleanup(os.close, fd)
        script = (
            "import os,sys,time; os.fchdir(int(sys.argv[1])); "
            "\nfor i in range(30):\n"
            " open(str(i)+'.bin','wb').write(b'x'*1048576); time.sleep(.025)\n"
            "open('success','w').write('escaped quota')\n"
        )
        def monitor():
            total = sum(p.stat().st_size for p in root.iterdir())
            if total > 8 * 1024 * 1024:
                raise ValueError('aggregate quota')
        with self.assertRaisesRegex(ValueError, 'aggregate quota'):
            run_bounded([sys.executable, '-c', script, str(fd)], env={'PATH': '/usr/bin:/bin'},
                timeout_seconds=10, stdout_cap=100, stderr_cap=100, pass_fds=(fd,), monitor=monitor)
        self.assertFalse((root / 'success').exists())

    def test_count_monitor_remains_active_after_child_closes_both_pipes(self):
        root = Path(self.temp.name) / 'count-quota'
        root.mkdir()
        fd = os.open(root, ws.DIRECTORY)
        script = (
            "import os,sys,time; os.fchdir(int(sys.argv[1])); os.close(1); os.close(2)\n"
            "for i in range(30):\n"
            " open(str(i),'w').write('x'); time.sleep(.025)\n"
            "open('success','w').write('escaped quota')\n"
        )
        try:
            with patch.object(ws, 'MAX_WORKSPACE_FILES', 4):
                with self.assertRaisesRegex(ValueError, 'quota'):
                    run_bounded([sys.executable, '-c', script, str(fd)], env={},
                        timeout_seconds=10, stdout_cap=100, stderr_cap=100,
                        pass_fds=(fd,), monitor=lambda: ws.check_quota(fd))
            self.assertFalse((root / 'success').exists())
        finally:
            os.close(fd)

    def test_project_entry_is_checked_at_receipt_link_publication(self):
        root = self.store.project_root(self.project_id)
        moved = root.with_name(root.name + '-detached-at-link')
        original = ws.write
        def replacing(fd, name, payload, *args, **kwargs):
            result = original(fd, name, payload, *args, **kwargs)
            if str(name).startswith('.v001.json.'):
                root.rename(moved)
                root.mkdir(mode=0o700)
            return result
        with patch.object(ws, 'write', side_effect=replacing):
            with self.assertRaisesRegex(ValueError, 'project.*identity'):
                self.action()
        self.assertFalse((moved / 'reelbench_evidence/v001.json').exists())
        self.assertFalse((root / 'reelbench_evidence/v001.json').exists())

    def test_mkdir_fstat_failure_closes_just_opened_child(self):
        root = Path(self.temp.name) / 'fd-test'
        root.mkdir()
        fd = os.open(root, ws.DIRECTORY)
        self.addCleanup(os.close, fd)
        original = os.fstat
        opened = []
        real_open = os.open
        def opening(*args, **kwargs):
            result = real_open(*args, **kwargs)
            opened.append(result)
            return result
        def failing(descriptor):
            if opened and descriptor == opened[-1]:
                raise RuntimeError('injected fstat')
            return original(descriptor)
        with patch('os.open', side_effect=opening), patch('os.fstat', side_effect=failing):
            with self.assertRaisesRegex(RuntimeError, 'injected fstat'):
                ws.mkdir(fd, 'child')
        for descriptor in opened:
            with self.assertRaises(OSError):
                original(descriptor)

    def test_nonseed_receipt_requires_parent_and_real_consumed_inputs(self):
        receipt = evidence('validate')
        receipt['parent_version'] = None
        receipt['parent_fingerprint'] = None
        receipt['evidence_fingerprint'] = canonical_fingerprint({k: v for k, v in receipt.items() if k != 'evidence_fingerprint'})
        with self.assertRaises(ValueError):
            validate_reelbench_evidence(receipt)

    def test_tool_identity_cannot_claim_arbitrary_trusted_path(self):
        receipt = evidence('seed')
        receipt['tool_identities'][0]['source_path'] = '/some/unrelated/node'
        receipt['commands'][0]['tool_identities'] = receipt['tool_identities']
        receipt['argv_fingerprint'] = canonical_fingerprint({'commands': receipt['commands']})
        receipt['evidence_fingerprint'] = canonical_fingerprint({k: v for k, v in receipt.items() if k != 'evidence_fingerprint'})
        with self.assertRaises(ValueError):
            validate_reelbench_evidence(receipt)

    def crash_action(self, visible=False):
        script = """
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
from tests.test_reelbench_project_service import ReelBenchProjectServiceTests
from scripts.video_project_store import VideoProjectStore
case = ReelBenchProjectServiceTests()
case.store = VideoProjectStore(Path(sys.argv[1]))
case.project_id = sys.argv[2]
case.temp = SimpleNamespace(name=sys.argv[3])
case.source = case.store.project_root(case.project_id) / 'source/source.mp4'
case.source_receipt = case.store.read_version(case.project_id, 'source_receipt', 'v001', 'source_receipt.schema.json')
service = case._service()
write = case.store.write_version
def crash(*args, **kwargs):
    if sys.argv[4] == 'visible':
        write(*args, **kwargs)
    os._exit(73)
case.store.write_version = crash
service.run(case.project_id, action='seed', expected_parent=None, source_receipt_version='v001')
"""
        result = subprocess.run([sys.executable, '-c', script, str(self.store._root), self.project_id,
            self.temp.name, 'visible' if visible else 'orphan'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 73, result.stderr)

    def test_hard_crash_orphan_is_recovered_before_retry(self):
        self.crash_action()
        result = self.action()
        self.assertEqual(result['version'], 'v001')
        self.assertFalse(any(p.name.startswith('.reelbench-work-') for p in self.store.project_root(self.project_id).iterdir()))

    def test_forged_operation_marker_blocks_without_deleting_orphan(self):
        self.crash_action()
        root = self.store.project_root(self.project_id)
        work = next(root.glob('.reelbench-work-*'))
        marker = work / '.reelbench-operation.json'
        data = json.loads(marker.read_text())
        data['action'] = 'render'
        marker.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'marker|recovery'):
            self.action()
        self.assertTrue((root / 'reelbench/v001/shots.json').exists())

    def test_visible_crash_receipt_is_preserved_and_reported_as_indeterminate(self):
        self.crash_action(visible=True)
        with self.assertRaises(VersionCommitIndeterminateError) as caught:
            self.action()
        recovered = self.service.reconcile_indeterminate(caught.exception)
        self.assertEqual(recovered['version'], 'v001')

    def test_live_orphan_owner_is_not_cleaned(self):
        self.crash_action()
        work = next(self.store.project_root(self.project_id).glob('.reelbench-work-*'))
        descriptor = os.open(work, ws.DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'live owner'):
                self.action()
            self.assertTrue((work / '.reelbench-operation.json').exists())
        finally:
            os.close(descriptor)

    def test_unknown_orphan_directory_is_not_deleted(self):
        root = self.store.project_root(self.project_id)
        (root / 'reelbench/v001').mkdir(parents=True, mode=0o700)
        (root / 'reelbench/v001/foreign').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'orphan'):
            self.action()
        self.assertEqual((root / 'reelbench/v001/foreign').read_text(), 'keep')

    def test_custom_executor_never_allows_omitting_trusted_store(self):
        from scripts.reelbench_adapter import ReelBenchAdapter
        from functools import partial
        with self.assertRaisesRegex(ValueError, 'trusted'):
            ReelBenchAdapter(project_root=self.store.project_root(self.project_id),
                shots_script=self.service._adapter._shots_script,
                tools=self.service._adapter._tools, runner=partial(run_bounded))

    def test_directory_previous_close_failure_does_not_leak_new_child(self):
        root = self.store.project_root(self.project_id)
        fd = os.open(root, ws.DIRECTORY)
        original_open, original_dup, original_close = os.open, os.dup, os.close
        owned = set()
        failed = []
        def opening(*args, **kwargs):
            item = original_open(*args, **kwargs); owned.add(item); return item
        def duplicating(item):
            item = original_dup(item); owned.add(item); return item
        def closing(item):
            if not failed:
                failed.append(item)
                raise RuntimeError('previous close failed')
            original_close(item); owned.discard(item)
        try:
            with patch('os.open', side_effect=opening), patch('os.dup', side_effect=duplicating), patch('os.close', side_effect=closing):
                with self.assertRaisesRegex(RuntimeError, 'previous close failed'):
                    with ws.directory(fd, 'source'):
                        pass
            self.assertEqual(owned, set())
        finally:
            original_close(fd)

    def test_aggregate_quota_includes_preexisting_files_before_launch(self):
        root = self.store.project_root(self.project_id)
        fd = os.open(root, ws.DIRECTORY)
        try:
            with patch.object(ws, 'MAX_TREE_BYTES', 1):
                with self.assertRaisesRegex(ValueError, 'aggregate'):
                    run_bounded([sys.executable, '-c', "raise AssertionError('launched')"],
                        env={}, timeout_seconds=5, stdout_cap=100, stderr_cap=100,
                        monitor=lambda: ws.check_quota(fd))
        finally:
            os.close(fd)

    def test_resigned_evidence_frame_ids_must_match_parsed_shots(self):
        seed = self.action()
        receipt = self.action('evidence', seed['version'])
        for artifact in receipt['artifacts']:
            artifact['path'] = artifact['path'].replace('S01', 'S99')
        receipt['evidence_fingerprint'] = canonical_fingerprint({k: v for k, v in receipt.items() if k != 'evidence_fingerprint'})
        with self.assertRaises(ValueError):
            validate_reelbench_evidence(receipt)

    def test_resigned_script_digest_cannot_change_the_pinned_program(self):
        receipt = evidence('seed')
        receipt['script_manifest'][0]['sha256'] = '0' * 64
        receipt['commands'][0]['script_manifest'] = receipt['script_manifest']
        receipt['argv_fingerprint'] = canonical_fingerprint({'commands': receipt['commands']})
        receipt['evidence_fingerprint'] = canonical_fingerprint({k: v for k, v in receipt.items() if k != 'evidence_fingerprint'})
        with self.assertRaises(ValueError):
            validate_reelbench_evidence(receipt)

    def test_resigned_receipt_cannot_omit_track_or_frame_inputs(self):
        receipt = evidence('validate')
        receipt['consumed_artifacts'] = [a for a in receipt['consumed_artifacts'] if a['workspace_path'] != 'inputs/track.json']
        receipt['evidence_fingerprint'] = canonical_fingerprint({k: v for k, v in receipt.items() if k != 'evidence_fingerprint'})
        with self.assertRaises(ValueError):
            validate_reelbench_evidence(receipt)

    def test_replacement_writer_waits_for_original_store_lock(self):
        entered, release = threading.Event(), threading.Event()
        runner = self.service._adapter._runner
        other = self._service()
        root = self.store.project_root(self.project_id)
        def replacing(argv, **kwargs):
            result = runner(argv, **kwargs)
            moved = root.with_name(root.name + '-moved')
            root.rename(moved)
            shutil.copytree(moved, root)
            entered.set()
            if not release.wait(5):
                raise AssertionError('test release timeout')
            return result
        self.service._adapter._runner = replacing
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.action)
            self.assertTrue(entered.wait(5))
            second = pool.submit(other.run, self.project_id, action='seed', expected_parent=None,
                source_receipt_version='v001')
            try:
                with self.assertRaises(concurrent.futures.TimeoutError):
                    second.result(.1)
            finally:
                release.set()
            with self.assertRaisesRegex(ValueError, 'project.*identity'):
                first.result(5)
            with self.assertRaisesRegex(ValueError, 'marker'):
                second.result(5)
        self.assertFalse((root / 'reelbench_evidence/v001.json').exists())

    def test_workspace_descriptor_fault_matrix_balances_every_acquired_handle(self):
        real = {name: getattr(os, name) for name in ('open', 'dup', 'fstat', 'close')}
        def execute(kind, fd, root):
            if kind == 'mkdir':
                ws.mkdir(fd, 'new/child')
            elif kind == 'copy':
                ws.copy(fd, 'input', fd, 'out', maximum=100)
            elif kind == 'file':
                with ws.file_at(fd, 'input') as child:
                    os.fstat(child)
            elif kind == 'directory':
                with ws.directory(fd, 'sub') as child:
                    os.fstat(child)
            else:
                ws._close_owned([ws.open_absolute(root / 'sub')])
        for kind in ('mkdir', 'copy', 'file', 'directory', 'absolute'):
            for failing in ('open', 'dup', 'fstat', 'close'):
                for nth in range(1, 17):
                    with self.subTest(kind=kind, failing=failing, nth=nth), tempfile.TemporaryDirectory(dir=self.temp.name) as scratch:
                        root = Path(scratch).resolve()
                        (root / 'sub').mkdir(mode=0o700)
                        (root / 'input').write_bytes(b'input')
                        fd = real['open'](root, ws.DIRECTORY)
                        owned, count, injected = set(), [0], [False]
                        def call(name, *args, **kwargs):
                            if name == failing:
                                count[0] += 1
                                if count[0] == nth:
                                    injected[0] = True
                                    raise RuntimeError('injected descriptor fault')
                            result = real[name](*args, **kwargs)
                            if name in {'open', 'dup'}:
                                owned.add(result)
                            elif name == 'close':
                                owned.discard(args[0])
                            return result
                        try:
                            with patch('os.open', side_effect=lambda *a, **k: call('open', *a, **k)), \
                                 patch('os.dup', side_effect=lambda *a, **k: call('dup', *a, **k)), \
                                 patch('os.fstat', side_effect=lambda *a, **k: call('fstat', *a, **k)), \
                                 patch('os.close', side_effect=lambda *a, **k: call('close', *a, **k)):
                                try:
                                    execute(kind, fd, root)
                                except RuntimeError as exc:
                                    self.assertEqual(str(exc), 'injected descriptor fault')
                                    self.assertTrue(injected[0])
                            self.assertEqual(owned, set())
                        finally:
                            for leaked in owned:
                                real['close'](leaked)
                            real['close'](fd)
