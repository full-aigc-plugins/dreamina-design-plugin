import os
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.task_service import TaskService

class Adapter:
    def __init__(self): self.calls=[]
    def run(self,args): self.calls.append(args); return type('R',(),{'exit_code':0,'payload':{'gen_status':'success','items':[]}})()

class TaskServiceTests(unittest.TestCase):
    def test_unknown_submit_queries_once_without_resubmit(self):
        a=Adapter(); result=TaskService(a).query('external-1')
        self.assertEqual(a.calls,[['query_result','--submit_id','external-1']]); self.assertEqual(result['provenance'],'externally-queried')
    def test_list_tasks_closed_filters(self):
        a=Adapter(); TaskService(a).list_tasks({'gen_status':'success'},limit=20)
        self.assertEqual(a.calls,[['list_task','--gen_status','success','--limit','20']])
    def test_unknown_filter_rejected(self):
        with self.assertRaises(ValueError): TaskService(Adapter()).list_tasks({'shell':'x'},limit=20)

    def test_query_rejects_nonzero_exit_and_unknown_or_malformed_status(self):
        for exit_code, payload in ((1, {'gen_status':'success'}), (0, {'gen_status':'mystery'}), (0, {})):
            class Bad:
                def run(self, args): return type('R', (), {'exit_code': exit_code, 'payload': payload})()
            with self.subTest(exit_code=exit_code, payload=payload), self.assertRaises(ValueError):
                TaskService(Bad()).query('external-1')

    def test_query_normalizes_real_cli_status_variants(self):
        for raw, expected in (('processing', 'querying'), ('pending', 'querying'), ('succeeded', 'success'), ('failure', 'failed')):
            class Variant:
                def run(self, args): return type('R', (), {'exit_code': 0, 'payload': {'gen_status': raw}})()
            self.assertEqual(TaskService(Variant()).query('external-1')['status'], expected)

    def test_download_directory_replacement_after_enumeration_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "download-001"
            target.mkdir(mode=0o700)

            class Downloading(Adapter):
                def run(self, args):
                    (target / "clip.mp4").write_bytes(b"\0\0\0\x18ftypisom" + b"x" * 20)
                    return super().run(args)

            real_listdir = os.listdir
            calls = 0

            def replace_after_listdir(path):
                nonlocal calls
                names = real_listdir(path)
                calls += 1
                if calls == 2:
                    target.rename(parent / "pinned-original")
                    target.mkdir(mode=0o700)
                return names

            with patch("scripts.task_service.os.listdir", side_effect=replace_after_listdir):
                with self.assertRaisesRegex(ValueError, "changed"):
                    TaskService(Downloading()).query("external-1", download_dir=str(target))

    def test_artifact_replacement_after_hash_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "download-001"
            target.mkdir(mode=0o700)

            class Downloading(Adapter):
                def run(self, args):
                    (target / "clip.mp4").write_bytes(b"\0\0\0\x18ftypisom" + b"x" * 20)
                    return super().run(args)

            real_read = os.read
            replaced = False

            def replace_after_hash(fd, size):
                nonlocal replaced
                chunk = real_read(fd, size)
                if not chunk and not replaced:
                    replacement = target / "replacement.mp4"
                    replacement.write_bytes(b"\0\0\0\x18ftypisom" + b"y" * 20)
                    replacement.replace(target / "clip.mp4")
                    replaced = True
                return chunk

            with patch("scripts.task_service.os.read", side_effect=replace_after_hash):
                with self.assertRaisesRegex(ValueError, "changed"):
                    TaskService(Downloading()).query("external-1", download_dir=str(target))

    def test_existing_broad_download_directory_fails_without_chmod(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "download-001"
            target.mkdir(mode=0o755)
            with self.assertRaisesRegex(ValueError, "0700"):
                TaskService(Adapter()).query("external-1", download_dir=str(target))
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)

    def test_same_size_wrong_canonical_collision_is_rejected_without_mutation(self):
        media = b"\0\0\0\x18ftypisom" + b"x" * 20
        digest = hashlib.sha256(media).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "download-001"; target.mkdir(mode=0o700)
            collision = target / f"artifact-{digest}.mp4"
            collision.write_bytes(b"\0\0\0\x18ftypisom" + b"y" * 20); collision.chmod(0o400)
            before = collision.read_bytes()
            class Downloading(Adapter):
                def run(self, args):
                    (target / "clip.mp4").write_bytes(media)
                    return super().run(args)
            with self.assertRaisesRegex(ValueError, "collision"):
                TaskService(Downloading()).query("external-1", download_dir=str(target))
            self.assertEqual(collision.read_bytes(), before)

    def test_recovery_returns_one_verified_canonical_and_ignores_temp(self):
        media = b"\0\0\0\x18ftypisom" + b"x" * 20
        digest = hashlib.sha256(media).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "download-001"; target.mkdir(mode=0o700)
            canonical = target / f"artifact-{digest}.mp4"; canonical.write_bytes(media); canonical.chmod(0o400)
            (target / ".verified-stale.tmp").write_bytes(b"partial")
            receipts = TaskService(Adapter()).verify_download_dir(str(target))
            self.assertEqual([item["sha256"] for item in receipts], [digest])
            self.assertFalse((target / ".verified-stale.tmp").exists())

    def test_canonical_extension_must_match_verified_media_type(self):
        media = b"\x89PNG\r\n\x1a\n" + b"x" * 24
        digest = hashlib.sha256(media).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "download-001"; target.mkdir(mode=0o700)
            disguised = target / f"artifact-{digest}.mp4"
            disguised.write_bytes(media); disguised.chmod(0o400)
            with self.assertRaisesRegex(ValueError, "collision"):
                TaskService(Adapter()).verify_download_dir(str(target))
