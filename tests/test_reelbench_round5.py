"""Recovery durability barriers and exact visible-commit failure identity."""
import json
import os
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from scripts import reelbench_workspace as ws
from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_operation import OperationJournal, ReelBenchRecoveryRequiredError
from scripts.video_project_store import VersionCommitIndeterminateError
from tests import test_reelbench_round3 as prior


class ReelBenchRoundFiveTests(unittest.TestCase):
    def case(self, *, visible=False, resume=False):
        case = prior.ReelBenchRoundThreeTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.crash_action(visible=visible)
        if resume:
            # Leave a real sealed cleanup record before any removal occurs.
            with case.service._locked_project(case.project_id) as (store, project, root, guard):
                journal = OperationJournal(store, project, case.project_id, root)
                work_name = next(root.glob('.reelbench-work-*')).name
                with ws.directory(project, work_name) as work:
                    marker = journal._read(work, work_name)
                journal._save_cleanup({'marker': marker, 'phase': 'workspace', 'published': True})
        return case

    def test_absent_deleted_directory_is_fsynced_before_cleanup_phase_advances(self):
        for target in ('output', 'workspace'):
            with self.subTest(target=target):
                case = self.case()
                root = case.store.project_root(case.project_id)
                work = next(root.glob('.reelbench-work-*'))
                directory = root / 'reelbench/v001' if target == 'output' else work
                parent_inode = directory.parent.stat().st_ino
                removed = [False]
                rmdir, fsync, save = os.rmdir, os.fsync, OperationJournal._save_cleanup

                def removing(name, **kwargs):
                    result = rmdir(name, **kwargs)
                    if name == directory.name and os.fstat(kwargs['dir_fd']).st_ino == parent_inode:
                        removed[0] = True
                    return result

                def failing(fd):
                    if removed[0] and os.fstat(fd).st_ino == parent_inode:
                        raise OSError('parent durability fault after rmdir')
                    return fsync(fd)

                with patch('os.rmdir', side_effect=removing), patch('os.fsync', side_effect=failing):
                    with self.assertRaises(ReelBenchRecoveryRequiredError):
                        case.action()
                self.assertTrue(removed[0])
                self.assertFalse(directory.exists())
                intent = next(root.glob('.reelbench-cleanup-*'))
                self.assertEqual(json.loads(intent.read_text())['phase'], target)
                advanced = 'workspace' if target == 'output' else 'complete'
                durable = [False]

                def saving(journal, record):
                    if record['phase'] == advanced:
                        self.assertTrue(durable[0], 'cleanup phase advanced before absent-entry parent fsync')
                    return save(journal, record)

                # Repeated fsync failure must leave the old phase and workspace marker intact.
                with patch.object(OperationJournal, '_save_cleanup', new=saving), patch('os.fsync', side_effect=failing):
                    with self.assertRaises(ReelBenchRecoveryRequiredError):
                        case.action()
                self.assertEqual(json.loads(intent.read_text())['phase'], target)
                if target == 'output':
                    self.assertTrue((work / '.reelbench-operation.json').exists())

                def syncing(fd):
                    result = fsync(fd)
                    if os.fstat(fd).st_ino == parent_inode:
                        durable[0] = True
                    return result

                with patch.object(OperationJournal, '_save_cleanup', new=saving), patch('os.fsync', side_effect=syncing):
                    self.assertEqual(case.action()['version'], 'v001')
                self.assertTrue(durable[0])
                self.assertFalse(intent.exists())

    @contextmanager
    def cleanup_fault(self, stage, failure, before_failure):
        def fail(*args, **kwargs):
            before_failure()
            raise failure

        with ExitStack() as stack:
            if stage.startswith('save-'):
                _, phase, nth = stage.split('-')
                original, fsync = OperationJournal._save_cleanup, os.fsync
                count = [0]

                def failing(fd):
                    count[0] += 1
                    if count[0] == int(nth):
                        fail()
                    return fsync(fd)

                def saving(journal, record):
                    if record['phase'] == phase:
                        with patch('os.fsync', side_effect=failing):
                            return original(journal, record)
                    return original(journal, record)

                stack.enter_context(patch.object(OperationJournal, '_save_cleanup', new=saving))
            elif stage == 'tree':
                stack.enter_context(patch.object(ws, 'remove_tree', side_effect=fail))
            else:
                unlink, rmdir, fsync = os.unlink, os.rmdir, os.fsync
                removed = [False]

                def unlinking(name, **kwargs):
                    if stage == 'operation-unlink' and name == '.reelbench-operation.json':
                        fail()
                    if name.startswith('.reelbench-cleanup-'):
                        if stage == 'cleanup-unlink':
                            fail()
                        result = unlink(name, **kwargs)
                        removed[0] = True
                        return result
                    return unlink(name, **kwargs)

                def removing(name, **kwargs):
                    if stage == 'workspace-rmdir' and name.startswith('.reelbench-work-'):
                        fail()
                    result = rmdir(name, **kwargs)
                    if stage == 'workspace-fsync' and name.startswith('.reelbench-work-'):
                        removed[0] = True
                    if stage == 'nested-fsync' and not name.startswith('.reelbench-work-'):
                        removed[0] = True
                    return result

                def syncing(fd):
                    if stage in ('cleanup-fsync', 'workspace-fsync', 'nested-fsync') and removed[0]:
                        fail()
                    return fsync(fd)

                stack.enter_context(patch('os.unlink', side_effect=unlinking))
                stack.enter_context(patch('os.rmdir', side_effect=removing))
                stack.enter_context(patch('os.fsync', side_effect=syncing))
            yield

    def test_verified_visible_receipt_retains_exact_indeterminate_on_every_cleanup_failure(self):
        for resume in (False, True):
            stages = ['tree', 'operation-unlink', 'workspace-rmdir', 'workspace-fsync',
                'nested-fsync', 'cleanup-unlink', 'cleanup-fsync']
            phases = ('complete',) if resume else ('workspace', 'complete')
            stages += [f'save-{phase}-{nth}' for phase in phases for nth in (1, 2, 3)]
            for stage in stages:
                with self.subTest(resume=resume, stage=stage):
                    case = self.case(visible=True, resume=resume)
                    root = case.store.project_root(case.project_id)
                    receipt_path = root / 'reelbench_evidence/v001.json'
                    receipt_bytes = receipt_path.read_bytes()
                    payload_fingerprint = canonical_fingerprint(json.loads(receipt_bytes))
                    artifacts = {p: p.read_bytes() for p in (root / 'reelbench/v001').rglob('*') if p.is_file()}
                    failure = RuntimeError('cleanup fault') if stage == 'tree' else OSError('cleanup fault')
                    created = []
                    initialize = VersionCommitIndeterminateError.__init__

                    def initializing(error, **kwargs):
                        initialize(error, **kwargs)
                        created.append(error)

                    def before_failure():
                        self.assertEqual(len(created), 1, 'exact indeterminate must exist before cleanup fails')

                    with patch.object(VersionCommitIndeterminateError, '__init__', new=initializing):
                        with self.cleanup_fault(stage, failure, before_failure):
                            with self.assertRaises(VersionCommitIndeterminateError) as caught:
                                case.action()
                    error = caught.exception
                    self.assertEqual(len(created), 1)
                    self.assertIs(error, created[0])
                    self.assertIs(error.__cause__, failure)
                    self.assertEqual((error.project_id, error.family, error.version, error.path, error.payload_fingerprint),
                        (case.project_id, 'reelbench_evidence', 'v001', receipt_path, payload_fingerprint))
                    self.assertEqual(receipt_path.read_bytes(), receipt_bytes)
                    self.assertEqual({p: p.read_bytes() for p in artifacts}, artifacts)
                    self.assertEqual(case.service.reconcile_indeterminate(error)['version'], 'v001')

    def test_unverified_visible_receipt_never_becomes_indeterminate(self):
        for resume in (False, True):
            for corrupt in ('receipt', 'artifact', 'seal', 'semantic'):
                with self.subTest(resume=resume, corrupt=corrupt):
                    case = self.case(visible=True, resume=resume)
                    root = case.store.project_root(case.project_id)
                    receipt = root / 'reelbench_evidence/v001.json'
                    artifact = root / 'reelbench/v001/shots.json'
                    if corrupt == 'receipt':
                        receipt.write_text('{}')
                    elif corrupt == 'artifact':
                        artifact.write_text('{}')
                    elif corrupt == 'semantic':
                        # Re-sign both fingerprints so only real receipt/artifact semantics reject it.
                        payload = json.loads(receipt.read_text())
                        payload['shot_ids'] = ['unbacked']
                        payload['evidence_fingerprint'] = canonical_fingerprint({k: v for k, v in payload.items() if k != 'evidence_fingerprint'})
                        receipt.write_text(json.dumps(payload))
                        with case.service._locked_project(case.project_id) as (store, project, root, guard):
                            journal = OperationJournal(store, project, case.project_id, root)
                            work_name = next(root.glob('.reelbench-work-*')).name
                            with ws.directory(project, work_name) as work:
                                marker = journal._read(work, work_name)
                                journal.bind_receipt(work, marker, payload)
                                marker = journal._read(work, work_name)
                            if resume:
                                journal._save_cleanup({'marker': marker, 'phase': 'workspace', 'published': True})
                    else:
                        marker = next(root.glob('.reelbench-cleanup-*')) if resume else next(root.glob('.reelbench-work-*')) / '.reelbench-operation.json'
                        payload = json.loads(marker.read_text())
                        payload['seal'] = '0' * 64
                        marker.write_text(json.dumps(payload))
                    with self.assertRaises(ReelBenchRecoveryRequiredError):
                        case.action()
                    self.assertTrue(receipt.exists())
                    self.assertTrue(artifact.exists())
