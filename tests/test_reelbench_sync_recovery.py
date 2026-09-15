"""Fault injection at publication boundaries, using real store/staging/probes."""
import copy
import json
import os
import struct
import shutil
import threading
import types
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch
from scripts import reelbench_workspace as ws
from scripts.bounded_process import BoundedProcessResult
from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_sync_operation import SyncOperationJournal, ReelBenchRecoveryRequiredError
from scripts.video_project_store import VersionCommitIndeterminateError
from tests.reelbench_sync_fixture import SyncFixture

def png(width, height):
    def chunk(kind, data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\x44\x44\x44'*width)*height))+chunk(b'IEND',b'')

class SyncRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = SyncFixture(self)
        real_runner = self.f.adapter._runner
        def runner(argv, **kwargs):
            if argv[6:9] == ['tools/node','script/video-sync.mjs','panels']:
                fd = int(argv[4])
                layout = {'panel':{'width':640,'height':288},'view':{'x':14,'y':40,'width':610,'height':222},
                    'content':63,'rows':[{'id':'S01','top':0,'height':63}],'tallHeight':288}
                ws.write(fd,'output/panels/layout.json',json.dumps(layout).encode())
                for name in ('static.png','list-dim.png','list-lit.png'): ws.write(fd,'output/panels/'+name,png(640,288))
                return BoundedProcessResult(0,'','')
            return real_runner(argv,**kwargs)
        self.f.adapter._runner = runner
        @contextmanager
        def lease():
            yield types.SimpleNamespace(context=types.SimpleNamespace(executable=self.f.trust.resolve_verified('node'), verified_at='2026-09-15T00:00:00Z'), proxy_environment={},pass_fds=())
        self.f.service._trusted_tools = types.SimpleNamespace(reverify_browser_for_sync_lease=lease)

    def test_parallel_actions_publish_distinct_immutable_versions(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:self.f.service.panels(*self.f.args), range(2)))
        self.assertEqual({r['version'] for r in results},{'v001','v002'})
        root = self.f.project.store.project_root(self.f.args[0])
        self.assertEqual(len(list((root/'reelbench_sync_media').iterdir())),2)

    def test_visible_commit_error_reconciles_exact_media_without_rerun(self):
        original = self.f.project.store.write_version
        def fail(*args,**kwargs):
            receipt = original(*args,**kwargs)
            if args[1] == 'reelbench_sync': raise OSError('after-visible failure')
            return receipt
        with patch.object(self.f.project.store,'write_version',fail):
            with self.assertRaises(VersionCommitIndeterminateError) as caught: self.f.service.panels(*self.f.args)
        receipt = self.f.service.reconcile_indeterminate(caught.exception)
        self.assertEqual(receipt['version'],'v001')
        original_error = caught.exception
        wrong = VersionCommitIndeterminateError(project_id=original_error.project_id, family=original_error.family,
            version=original_error.version, path=original_error.path, payload_fingerprint='a'*64)
        with self.assertRaises(ValueError): self.f.service.reconcile_indeterminate(wrong)

    def test_cleanup_crash_preserves_sealed_recovery_and_published_media(self):
        with patch.object(SyncOperationJournal,'_finish_cleanup',side_effect=OSError('crash during cleanup')):
            with self.assertRaises(VersionCommitIndeterminateError) as caught: self.f.service.panels(*self.f.args)
        root = self.f.project.store.project_root(self.f.args[0])
        self.assertEqual(len(list(root.glob('.reelbench-sync-cleanup-*'))),1)
        with self.assertRaises(VersionCommitIndeterminateError): self.f.service.panels(*self.f.args)
        self.assertEqual(list(root.glob('.reelbench-sync-cleanup-*')),[])
        self.assertEqual(self.f.service.reconcile_indeterminate(caught.exception)['version'],'v001')

    def test_unsealed_workspace_blocks_instead_of_deleting(self):
        root = self.f.project.store.project_root(self.f.args[0])
        orphan = root / ('.reelbench-sync-work-'+'a'*24)
        orphan.mkdir(mode=0o700)
        (orphan/'valuable').write_bytes(b'private')
        with self.assertRaises(ReelBenchRecoveryRequiredError): self.f.service.panels(*self.f.args)
        self.assertEqual((orphan/'valuable').read_bytes(),b'private')

    def test_setup_failure_closes_all_descriptors(self):
        def fds():
            import fcntl
            found=set()
            for fd in range(512):
                try: fcntl.fcntl(fd,fcntl.F_GETFD); found.add(fd)
                except OSError: pass
            return found
        before=fds()
        with patch.object(SyncOperationJournal,'create',side_effect=RuntimeError('setup fault')):
            with self.assertRaisesRegex(RuntimeError,'setup fault'): self.f.service.plan(*self.f.args)
        self.assertEqual(fds(),before)

    def test_recomputed_receipt_with_unknown_fields_is_rejected(self):
        receipt=self.f.service.panels(*self.f.args)
        receipt['injected']=True
        receipt['evidence_fingerprint']=canonical_fingerprint({k:v for k,v in receipt.items() if k!='evidence_fingerprint'})
        root=self.f.project.store.project_root(self.f.args[0])
        self.f.project.store._atomic_write(root/'reelbench_sync/v001.json',receipt)
        with self.assertRaisesRegex(ValueError,'fields'): self.f.service.panels(*self.f.args)

    def test_preexec_tampered_preload_rejected_before_media_process(self):
        from scripts.reelbench_adapter import ReelBenchAdapterError
        original = self.f.adapter._runner
        changed = False
        def tamper(argv, **kwargs):
            nonlocal changed
            if not changed:
                changed=True
                fd=int(argv[4])
                with ws.directory(fd,'script') as directory:
                    os.chmod('browser-lease.mjs',0o600,dir_fd=directory)
                    opened=os.open('browser-lease.mjs',os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW,dir_fd=directory)
                    try: os.write(opened,b'process.exit(0);')
                    finally: os.close(opened)
            return original(argv,**kwargs)
        self.f.adapter._runner=tamper
        with self.assertRaises(ReelBenchAdapterError): self.f.service.panels(*self.f.args)
        self.assertIsNone(self.f.service.status(self.f.args[0])['latest_version'])

    def test_project_swap_is_rejected_without_publishing_to_replacement(self):
        root=self.f.project.store.project_root(self.f.args[0])
        moved=root.with_name(root.name+'-moved')
        original=self.f.adapter._runner
        changed=False
        def swap(argv,**kwargs):
            nonlocal changed
            if not changed:
                changed=True
                root.rename(moved)
                root.mkdir(mode=0o700)
            return original(argv,**kwargs)
        self.f.adapter._runner=swap
        try:
            with self.assertRaises(ValueError): self.f.service.plan(*self.f.args)
            self.assertEqual(list(root.iterdir()),[])
        finally:
            if moved.exists():
                root.rmdir()
                moved.rename(root)

    def test_sync_script_and_mutable_lock_joint_tamper_is_rejected(self):
        root=self.f.root/'plugin'
        original=self.f.adapter._shots_script.parents[3]
        for skill in ('dreamina-video-shots','dreamina-video-sync'):
            target=root/'skills'/skill/'scripts'
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copytree(original/'skills'/skill/'scripts',target)
        (root/'upstream').mkdir()
        lock=json.loads((original/'upstream/reelbench.lock.json').read_text())
        altered=root/'skills/dreamina-video-sync/scripts/panel.css'
        altered.write_bytes(altered.read_bytes()+b'/* tamper */')
        import hashlib
        lock['files']['video-sync/scripts/panel.css']['packaged_sha256']=hashlib.sha256(altered.read_bytes()).hexdigest()
        (root/'upstream/reelbench.lock.json').write_text(json.dumps(lock))
        self.f.adapter._shots_script=root/'skills/dreamina-video-shots/scripts/video-shots.mjs'
        self.f.adapter._sync_script=root/'skills/dreamina-video-sync/scripts/video-sync.mjs'
        with self.assertRaisesRegex(ValueError,'independent pinned'): self.f.service.plan(*self.f.args)
