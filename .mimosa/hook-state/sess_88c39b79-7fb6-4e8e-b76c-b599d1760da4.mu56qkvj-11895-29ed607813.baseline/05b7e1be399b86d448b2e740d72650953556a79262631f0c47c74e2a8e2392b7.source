"""Behavior regressions for sync project ownership and real process boundaries."""
import inspect
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.reelbench_sync_service import ReelBenchSyncService


class SyncTrustTests(unittest.TestCase):
    def test_sync_monitor_allows_large_video_but_prevents_oversized_panel(self):
        from scripts import reelbench_workspace as ws
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'output').mkdir(mode=0o700)
            fd=os.open(root,ws.DIRECTORY)
            try:
                with (root/'output/review.mp4').open('wb') as file: file.truncate(300*1024**2)
                ws.check_sync_action_quota(fd)
                with (root/'output/static.png').open('wb') as file: file.truncate(65*1024**2)
                with self.assertRaises(ws.WorkspaceQuotaError): ws.check_sync_action_quota(fd)
            finally: os.close(fd)
    def test_preload_does_not_change_non_browser_spawn(self):
        from scripts.reelbench_adapter import BROWSER_PRELOAD
        with tempfile.TemporaryDirectory() as temporary:
            preload = Path(temporary)/'preload.mjs'
            preload.write_bytes(BROWSER_PRELOAD)
            result = subprocess.run(['node','--import',str(preload),'-e',
                'const c=require("child_process"); process.stdout.write(c.execFileSync("/usr/bin/python3",["-c","print(73)"],{encoding:"utf8"}));'],
                env={**os.environ,'REELBENCH_BROWSER_BUNDLE_FD':'invalid'},capture_output=True,text=True,timeout=10)
            self.assertEqual((result.returncode,result.stdout),(0,'73\n'))

    def test_ffmpeg_proxy_rejects_unexpected_argv_before_exec(self):
        from scripts.reelbench_sync_ffmpeg import SYNC_FFMPEG_PROXY
        result = subprocess.run(['/usr/bin/python3','-c',SYNC_FFMPEG_PROXY.decode(),'-y','-i','/foreign/source.mp4'],
            env={'REELBENCH_SYNC_DURATION':'8'},capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('unexpected compose input',result.stderr)

    def test_real_final_consumer_rejects_review_before_probing(self):
        from scripts.final_media_service import FinalMediaService, FinalMediaValidationError
        service = FinalMediaService(None)
        with self.assertRaisesRegex(FinalMediaValidationError, 'review'):
            service.verify_final(Path('/private/reelbench_sync_media/v001/review.mp4'), object())
        with self.assertRaisesRegex(FinalMediaValidationError, 'review'):
            service.export_verified({'artifact_role':'synchronized_review'}, source=Path('/private/source.mp4'), destination=Path('/private/final.mp4'))

    def test_public_service_cannot_accept_caller_media_or_workspace(self):
        for name in ('plan', 'panels', 'export', 'verify'):
            signature = inspect.signature(getattr(ReelBenchSyncService, name))
            self.assertIn('project_id', signature.parameters)
            self.assertNotIn('inputs', signature.parameters)

    def test_layout_rejects_injected_view_rows_and_unknown_fields(self):
        import copy
        from scripts.reelbench_sync_service import ReelBenchSyncError
        plan = {'panel':{'width':640,'height':288}}
        layout = {'panel':plan['panel'],'view':{'x':14,'y':40,'width':610,'height':222},
            'content':63,'rows':[{'id':'S01','top':0,'height':63}],'tallHeight':288}
        shots = {'shots':[{'id':'S01'}]}
        ReelBenchSyncService._validate_layout(layout, plan, shots)
        for mutate in (lambda x:x['view'].update(x=-1), lambda x:x['rows'][0].update(top=1.5),
                       lambda x:x.update(extra=True), lambda x:x.update(content=float('nan'))):
            changed = copy.deepcopy(layout)
            mutate(changed)
            with self.assertRaises(ReelBenchSyncError): ReelBenchSyncService._validate_layout(changed,plan,shots)

    def test_node_proxy_child_retains_both_lease_descriptors(self):
        from scripts.reelbench_adapter import BROWSER_PRELOAD
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'preload.mjs').write_bytes(BROWSER_PRELOAD)
            (root / 'tools').mkdir()
            proxy = root / 'tools/browser-proxy'
            proxy.write_text('#!/usr/bin/python3\nimport os\nprint(os.fstat(int(os.environ["REELBENCH_BROWSER_BUNDLE_FD"])).st_ino)\n')
            proxy.chmod(0o700)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                result = subprocess.run(['node', '--import', str(root / 'preload.mjs'), '-e',
                    'const c=require("child_process"); for(let i=0;i<4;i++) process.stdout.write(c.execFileSync("tools/browser-proxy",[],{encoding:"utf8"}));'],
                    cwd=root, env={**os.environ, 'REELBENCH_BROWSER_BUNDLE_FD': str(fd),
                        'REELBENCH_BROWSER_EXECUTABLE_FD': str(fd)}, pass_fds=(fd,), capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [str(os.fstat(fd).st_ino)] * 4)
            finally:
                os.close(fd)
