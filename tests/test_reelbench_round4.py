"""Regressions for store ancestry, reseeding, independent pins and recovery."""
import hashlib
import json
import os
import shutil
import sys
import subprocess
import concurrent.futures
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import reelbench_workspace as ws
from scripts.reelbench_operation import OperationJournal
from tests import test_reelbench_round3 as prior


class ReelBenchRoundFourTests(unittest.TestCase):
    setUp = prior.ReelBenchRoundThreeTests.setUp
    _service = prior.ReelBenchRoundThreeTests._service
    action = prior.ReelBenchRoundThreeTests.action
    crash_action = prior.ReelBenchRoundThreeTests.crash_action

    def test_store_and_ancestor_replacements_never_publish_detached_success(self):
        self._replacement(False)

    def test_full_ancestor_chain_replacement_never_publishes(self):
        self._replacement(True)

    def _replacement(self, ancestor):
        for ancestor in (ancestor,):
            with self.subTest(ancestor=ancestor):
                root = self.store._root.parent if ancestor else self.store._root
                moved = root.with_name(root.name + '-detached')
                runner = self.service._adapter._runner
                def replacing(argv, **kwargs):
                    result = runner(argv, **kwargs)
                    root.rename(moved)
                    root.mkdir(mode=0o700)
                    return result
                self.service._adapter._runner = replacing
                try:
                    with self.assertRaisesRegex(ValueError, 'store.*identity'):
                        self.action()
                    relative = Path('projects') / self.project_id if ancestor else Path(self.project_id)
                    self.assertFalse((moved / relative / 'reelbench_evidence/v001.json').exists())
                finally:
                    root.rmdir()
                    moved.rename(root)
                    self.service._adapter._runner = runner

    def test_store_entry_is_rechecked_at_receipt_link(self):
        root = self.store._root
        moved = root.with_name(root.name + '-link-replaced')
        original = ws.write
        def replacing(fd, name, payload, *args, **kwargs):
            result = original(fd, name, payload, *args, **kwargs)
            if str(name).startswith('.v001.json.'):
                root.rename(moved)
                root.mkdir(mode=0o700)
            return result
        try:
            with patch.object(ws, 'write', side_effect=replacing):
                with self.assertRaisesRegex(ValueError, 'store.*identity'):
                    self.action()
            self.assertFalse((moved / self.project_id / 'reelbench_evidence/v001.json').exists())
        finally:
            root.rmdir()
            moved.rename(root)

    def test_store_swap_after_receipt_is_typed_uncertainty_never_success(self):
        from scripts.video_project_store import VersionCommitIndeterminateError
        root = self.store._root
        moved = root.with_name(root.name + '-after-commit')
        original = self.store.write_version
        def replacing(*args, **kwargs):
            result = original(*args, **kwargs)
            root.rename(moved)
            root.mkdir(mode=0o700)
            return result
        try:
            with patch.object(self.store, 'write_version', side_effect=replacing):
                with self.assertRaises(VersionCommitIndeterminateError) as caught:
                    self.action()
            self.assertTrue((moved / self.project_id / 'reelbench_evidence/v001.json').exists())
            self.assertIn('store ancestry identity', str(caught.exception.__cause__))
        finally:
            root.rmdir()
            moved.rename(root)

    def test_replaced_store_writer_waits_and_cannot_commit_detached_success(self):
        other = self._service()
        entered, release = threading.Event(), threading.Event()
        runner = self.service._adapter._runner
        root = self.store._root
        moved = root.with_name(root.name + '-concurrent')
        def replacing(argv, **kwargs):
            result = runner(argv, **kwargs)
            root.rename(moved)
            shutil.copytree(moved, root)
            entered.set()
            if not release.wait(5):
                raise AssertionError('release timed out')
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
            with self.assertRaisesRegex(ValueError, 'store.*identity'):
                first.result(5)
            with self.assertRaisesRegex(ValueError, 'marker'):
                second.result(5)
        self.assertFalse((moved / self.project_id / 'reelbench_evidence/v001.json').exists())
        self.assertFalse((root / self.project_id / 'reelbench_evidence/v001.json').exists())

    def test_reseed_is_v002_bound_to_parent_and_resets_frames(self):
        first = self.action()
        second = self.action('seed', 'v001')
        self.assertEqual((first['version'], second['version']), ('v001', 'v002'))
        self.assertEqual(second['parent_fingerprint'], first['evidence_fingerprint'])
        self.assertEqual(len(second['consumed_artifacts']), 1)
        with self.assertRaisesRegex(ValueError, 'expected parent'):
            self.action('seed', 'v001')
        frames = self.action('evidence', 'v002')
        seed = self.action('seed', frames['version'])
        with self.assertRaisesRegex(ValueError, 'no frame evidence'):
            self.action('validate', seed['version'])

    def test_resigned_reseed_cannot_drop_or_skip_its_parent(self):
        from scripts.json_contracts import canonical_fingerprint
        from scripts.reelbench_contracts import validate_reelbench_evidence
        self.action()
        second = self.action('seed', 'v001')
        missing = {**second, 'parent_version': None, 'parent_fingerprint': None}
        skipped = {**second, 'version': 'v003', 'artifacts': [
            {**a, 'path': a['path'].replace('/v002/', '/v003/')} for a in second['artifacts']]}
        for changed in (missing, skipped):
            changed['evidence_fingerprint'] = canonical_fingerprint({k: v for k, v in changed.items() if k != 'evidence_fingerprint'})
            with self.assertRaises(ValueError):
                validate_reelbench_evidence(changed)

    def test_mutated_script_and_matching_lock_never_launch(self):
        for filename in ('video-shots.mjs', 'report.css', 'report.js'):
            with self.subTest(filename=filename):
                root = Path(self.temp.name).resolve() / filename.replace('.', '-')
                scripts = root / 'skills/dreamina-video-shots/scripts'
                scripts.mkdir(parents=True)
                original = self.service._adapter._shots_script
                for asset in ('video-shots.mjs', 'report.css', 'report.js'):
                    shutil.copyfile(original.parent / asset, scripts / asset)
                payload = (scripts / filename).read_bytes() + b'\n// changed pin\n'
                (scripts / filename).write_bytes(payload)
                lock = json.loads((original.parents[3] / 'upstream/reelbench.lock.json').read_text())
                lock['files']['video-shots/scripts/' + filename]['packaged_sha256'] = hashlib.sha256(payload).hexdigest()
                (root / 'upstream').mkdir()
                (root / 'upstream/reelbench.lock.json').write_text(json.dumps(lock))
                marker = root / 'launched'
                runner = self.service._adapter._runner
                def launching(*args, **kwargs):
                    marker.write_text('launched')
                    return runner(*args, **kwargs)
                self.service._adapter._shots_script = scripts / 'video-shots.mjs'
                self.service._adapter._runner = launching
                try:
                    with self.assertRaises(ValueError):
                        self.action()
                    self.assertFalse(marker.exists())
                finally:
                    self.service._adapter._shots_script = original
                    self.service._adapter._runner = runner

    def test_private_source_is_not_charged_to_generated_quota(self):
        # Logical sparse input exercises the boundary without a large allocation.
        root = Path(self.temp.name) / 'quota'
        root.mkdir()
        fd = os.open(root, ws.DIRECTORY)
        try:
            ws.mkdir(fd, 'source')
            with (root / 'source/video').open('wb') as stream:
                stream.truncate(256 * 1024 * 1024 + 1)
            ws.mkdir(fd, 'output')
            ws.check_action_quota(fd)
            with (root / 'output/generated').open('wb') as stream:
                stream.truncate(256 * 1024 * 1024 + 1)
            with self.assertRaisesRegex(ValueError, 'quota'):
                ws.check_action_quota(fd)
        finally:
            os.close(fd)

    def test_recovery_crash_after_output_removal_can_retry(self):
        self.crash_action()
        original = ws.remove_tree
        def crash(fd, name, **kwargs):
            original(fd, name, **kwargs)
            if name == 'v001':
                raise RuntimeError('recovery crashed after output removal')
        with patch.object(ws, 'remove_tree', side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.action()
        self.assertEqual(self.action()['version'], 'v001')

    def test_recovery_crash_mid_workspace_removal_can_retry(self):
        self.crash_action()
        original = os.unlink
        def crash(name, **kwargs):
            original(name, **kwargs)
            if name == '.reelbench-operation.json':
                raise RuntimeError('crash after operation marker deletion')
        with patch('os.unlink', side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.action()
        self.assertEqual(self.action()['version'], 'v001')

    def test_visible_recovery_crash_mid_workspace_cleanup_remains_reconcilable(self):
        from scripts.video_project_store import VersionCommitIndeterminateError
        self.crash_action(visible=True)
        original = os.unlink
        def crash(name, **kwargs):
            original(name, **kwargs)
            if name == '.reelbench-operation.json':
                raise RuntimeError('crash after operation marker deletion')
        with patch('os.unlink', side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.action()
        with self.assertRaises(VersionCommitIndeterminateError) as caught:
            self.action()
        self.assertEqual(self.service.reconcile_indeterminate(caught.exception)['version'], 'v001')

    def test_copy_accepts_exact_2gib_source_with_bounded_streaming(self):
        root = Path(self.temp.name) / 'stream'
        root.mkdir()
        with (root / 'large').open('wb') as stream:
            stream.truncate(2 * 1024**3)
        fd = os.open(root, ws.DIRECTORY)
        ws.mkdir(fd, 'work')
        destination = os.open(root / 'work', ws.DIRECTORY)
        largest = [0]
        def sparse_write(out, payload):
            largest[0] = max(largest[0], len(payload))
            position = os.lseek(out, len(payload), os.SEEK_CUR)
            os.ftruncate(out, position)
            return len(payload)
        try:
            with patch('os.write', side_effect=sparse_write):
                record, info = ws.copy(fd, 'large', destination, 'source/exact', maximum=2 * 1024**3, quota=True)
            self.assertEqual((record['size_bytes'], info.st_size), (2147483648, 2147483648))
            self.assertLessEqual(largest[0], 1048576)
            with (root / 'large').open('r+b') as stream:
                stream.truncate(2147483649)
            with self.assertRaisesRegex(ValueError, 'bound'):
                ws.copy(fd, 'large', destination, 'source/too-large', maximum=2 * 1024**3, quota=True)
            self.assertFalse((root / 'work/source/too-large').exists())
        finally:
            os.close(destination)
            os.close(fd)

    def test_seed_accepts_source_larger_than_generated_output_budget(self):
        self.source.chmod(0o600)
        with self.source.open('wb') as stream:
            stream.truncate(256 * 1024**2 + 1)
        self.source.chmod(0o400)
        with self.source.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.source_receipt = self.store.write_version(self.project_id, 'source_receipt',
            {**self.source_receipt, 'size_bytes': 268435457, 'source_sha256': digest},
            schema_name='source_receipt.schema.json')
        original = ws.copy
        def sparse_write(out, payload):
            position = os.lseek(out, len(payload), os.SEEK_CUR)
            os.ftruncate(out, position)
            return len(payload)
        def copying(root, name, destination, target, **kwargs):
            if target.startswith('source/'):
                with patch('os.write', side_effect=sparse_write):
                    return original(root, name, destination, target, **kwargs)
            return original(root, name, destination, target, **kwargs)
        with patch.object(ws, 'copy', side_effect=copying):
            receipt = self.action()
        self.assertEqual(receipt['consumed_artifacts'][0]['size_bytes'], 268435457)
        self.assertEqual(receipt['source_sha256'], digest)
        self.assertEqual(receipt['version'], 'v001')

    def test_cleanup_never_masks_action_error(self):
        def fail(*args, **kwargs):
            raise RuntimeError('primary action error')
        self.service._adapter._runner = fail
        with patch.object(self.service._adapter._tool_store, 'release', side_effect=OSError('tool cleanup error')):
            with patch.object(ws, 'remove_tree', side_effect=OSError('tree cleanup error')):
                with self.assertRaisesRegex(RuntimeError, 'primary action error') as caught:
                    self.action()
        notes = ' '.join(getattr(caught.exception, '__notes__', []))
        self.assertIn('cleanup', notes)

    def test_cleanup_only_failure_is_not_suppressed(self):
        from scripts.video_project_store import VersionCommitIndeterminateError
        with patch.object(self.service._adapter._tool_store, 'release', side_effect=OSError('release failed')):
            with self.assertRaises(VersionCommitIndeterminateError) as caught:
                self.action()
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(self.service.reconcile_indeterminate(caught.exception)['version'], 'v001')

    def test_store_chain_close_failure_after_commit_is_typed(self):
        from contextlib import contextmanager
        from scripts.video_project_store import VersionCommitIndeterminateError
        original = self.service._locked_project
        @contextmanager
        def failing(project_id):
            with original(project_id) as handles:
                yield handles
            raise OSError('store chain close failed')
        with patch.object(self.service, '_locked_project', new=failing):
            with self.assertRaises(VersionCommitIndeterminateError) as caught:
                self.action()
        self.assertIn('store chain close failed', str(caught.exception.__cause__))
        self.assertEqual(self.service.reconcile_indeterminate(caught.exception)['version'], 'v001')

    def test_every_durable_recovery_phase_survives_hard_exit(self):
        script = '''
import os, sys
from pathlib import Path
from types import SimpleNamespace
from tests.test_reelbench_project_service import ReelBenchProjectServiceTests
from scripts.video_project_store import VideoProjectStore
from scripts.reelbench_operation import OperationJournal
from scripts import reelbench_workspace as ws
case = ReelBenchProjectServiceTests()
case.store = VideoProjectStore(Path(sys.argv[1]))
case.project_id = sys.argv[2]
case.temp = SimpleNamespace(name=sys.argv[3])
case.source_receipt = case.store.read_version(case.project_id, 'source_receipt', 'v001', 'source_receipt.schema.json')
service = case._service()
target, when = sys.argv[4:6]
save, remove, unlink = OperationJournal._save_cleanup, ws.remove_tree, os.unlink
def injected(label, fn, *args, **kwargs):
    if target == label and when == 'before': os._exit(74)
    result = fn(*args, **kwargs)
    if target == label and when == 'after': os._exit(74)
    return result
def saving(self, record):
    return injected('seal-' + record['phase'], save, self, record)
def removing(fd, name, **kwargs):
    label = 'output' if name == 'v001' else 'workspace' if name.startswith('.reelbench-work-') else 'nested'
    return injected(label, remove, fd, name, **kwargs)
def unlinking(name, **kwargs):
    label = 'operation-marker' if name == '.reelbench-operation.json' else 'cleanup-marker' if name.startswith('.reelbench-cleanup-') else 'other'
    return injected(label, unlink, name, **kwargs)
OperationJournal._save_cleanup = saving
ws.remove_tree = removing
os.unlink = unlinking
service.run(case.project_id, action='seed', expected_parent=None, source_receipt_version='v001')
'''
        for phase in ('seal-output', 'output', 'seal-workspace', 'workspace', 'operation-marker', 'seal-complete', 'cleanup-marker'):
            for when in ('before', 'after'):
                with self.subTest(phase=phase, when=when):
                    case = prior.ReelBenchRoundThreeTests()
                    case.setUp()
                    self.addCleanup(case.doCleanups)
                    case.crash_action()
                    result = subprocess.run([sys.executable, '-c', script, str(case.store._root),
                        case.project_id, case.temp.name, phase, when], capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 74, result.stderr)
                    self.assertEqual(case.action()['version'], 'v001')

    def test_action_cleanup_crash_keeps_a_recoverable_sealed_intent(self):
        script = '''
import os, sys
from pathlib import Path
from types import SimpleNamespace
from tests.test_reelbench_project_service import ReelBenchProjectServiceTests
from scripts.video_project_store import VideoProjectStore
case = ReelBenchProjectServiceTests()
case.store = VideoProjectStore(Path(sys.argv[1]))
case.project_id = sys.argv[2]
case.temp = SimpleNamespace(name=sys.argv[3])
service = case._service()
if sys.argv[4] == 'failed':
    def fail(*args, **kwargs): raise RuntimeError('action publication failure')
    case.store.write_version = fail
unlink = os.unlink
def crash(name, **kwargs):
    unlink(name, **kwargs)
    if name == '.reelbench-operation.json': os._exit(75)
os.unlink = crash
service.run(case.project_id, action='seed', expected_parent=None, source_receipt_version='v001')
'''
        from scripts.video_project_store import VersionCommitIndeterminateError
        for mode in ('failed', 'published'):
            with self.subTest(mode=mode):
                case = prior.ReelBenchRoundThreeTests()
                case.setUp()
                self.addCleanup(case.doCleanups)
                result = subprocess.run([sys.executable, '-c', script, str(case.store._root),
                    case.project_id, case.temp.name, mode], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 75, result.stderr)
                if mode == 'failed':
                    self.assertEqual(case.action()['version'], 'v001')
                else:
                    with self.assertRaises(VersionCommitIndeterminateError) as caught:
                        case.action()
                    self.assertEqual(case.service.reconcile_indeterminate(caught.exception)['version'], 'v001')

    def test_recovery_phase_fsync_failures_are_repeatable(self):
        for phase in ('output', 'workspace', 'complete'):
            for nth in (1, 2, 3):
                with self.subTest(phase=phase, fsync=nth):
                    case = prior.ReelBenchRoundThreeTests()
                    case.setUp()
                    self.addCleanup(case.doCleanups)
                    case.crash_action()
                    original, fsync = OperationJournal._save_cleanup, os.fsync
                    count = [0]
                    def fail(fd):
                        count[0] += 1
                        if count[0] == nth:
                            raise OSError('phase fsync fault')
                        return fsync(fd)
                    def saving(journal, record):
                        if record['phase'] == phase:
                            with patch('os.fsync', side_effect=fail):
                                return original(journal, record)
                        return original(journal, record)
                    with patch.object(OperationJournal, '_save_cleanup', new=saving):
                        with self.assertRaisesRegex(ValueError, 'recovery'):
                            case.action()
                    self.assertGreaterEqual(count[0], nth)
                    self.assertEqual(case.action()['version'], 'v001')

    def test_descriptor_close_failure_is_not_allowed_to_mask_body_error(self):
        close = os.close
        failed = []
        def failing(fd):
            if not failed:
                failed.append(fd)
                raise OSError('descriptor cleanup fault')
            return close(fd)
        with patch('os.close', side_effect=failing):
            with self.assertRaisesRegex(RuntimeError, 'body error') as caught:
                with ws.absolute_chain(self.store._root):
                    raise RuntimeError('body error')
        self.assertIn('descriptor cleanup fault', ' '.join(caught.exception.__notes__))

    def test_cleanup_failure_is_attached_without_python311_exception_notes(self):
        class LegacyError(RuntimeError):
            add_note = None
        primary = LegacyError('original')
        secondary = OSError('cleanup')
        ws.cleanup_failure(primary, secondary)
        self.assertIs(primary.cleanup_errors[0], secondary)

    def test_metadata_cannot_bypass_live_aggregate_and_count_budgets(self):
        root = Path(self.temp.name) / 'metadata-quota'
        root.mkdir()
        fd = os.open(root, ws.DIRECTORY)
        try:
            for index in range(40):
                (root / str(index)).write_bytes(b'x' * 2000)
            with self.assertRaisesRegex(ValueError, 'metadata quota'):
                ws.check_action_quota(fd)
        finally:
            os.close(fd)

    def test_absolute_chain_fault_matrix_closes_every_owned_descriptor(self):
        from contextlib import ExitStack
        real = {name: getattr(os, name) for name in ('open', 'fstat', 'stat', 'close')}
        for failing in real:
            for nth in range(1, 24):
                with self.subTest(failing=failing, nth=nth):
                    owned, calls = set(), [0]
                    def call(name, *args, **kwargs):
                        if name == failing:
                            calls[0] += 1
                            if calls[0] == nth:
                                raise RuntimeError('injected chain failure')
                        result = real[name](*args, **kwargs)
                        if name == 'open': owned.add(result)
                        if name == 'close': owned.discard(args[0])
                        return result
                    try:
                        with ExitStack() as stack:
                            for name in real:
                                stack.enter_context(patch('os.' + name, side_effect=lambda *a, _name=name, **k: call(_name, *a, **k)))
                            try:
                                with ws.absolute_chain(self.store._root) as (_, _, guard):
                                    guard()
                            except RuntimeError as exc:
                                self.assertEqual(str(exc), 'injected chain failure')
                        self.assertEqual(owned, set())
                    finally:
                        for fd in owned: real['close'](fd)

    def test_live_output_monitor_kills_many_files_with_large_private_source(self):
        from scripts.bounded_process import run_bounded
        root = Path(self.temp.name) / 'live-output'
        root.mkdir()
        (root / 'source').mkdir()
        (root / 'output').mkdir()
        with (root / 'source/video').open('wb') as stream:
            stream.truncate(2147483648)
        fd = os.open(root, ws.DIRECTORY)
        script = "import os,sys,time; os.fchdir(int(sys.argv[1]))\nfor i in range(30):\n open('output/'+str(i),'wb').write(b'x'*1048576); time.sleep(.025)\nopen('output/success','w').write('escaped')"
        try:
            with patch.object(ws, 'MAX_TREE_BYTES', 8 * 1024**2):
                with self.assertRaisesRegex(ValueError, 'quota'):
                    run_bounded([sys.executable, '-c', script, str(fd)], env={}, timeout_seconds=10,
                        stdout_cap=100, stderr_cap=100, pass_fds=(fd,), monitor=lambda: ws.check_action_quota(fd))
            self.assertFalse((root / 'output/success').exists())
            self.assertGreater(len(list((root / 'output').iterdir())), 1)
        finally:
            os.close(fd)
