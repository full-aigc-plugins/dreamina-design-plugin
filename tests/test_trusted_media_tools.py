from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.trusted_media_tools import TrustedMediaToolError, TrustedMediaToolStore


class _Approve:
    def confirm_media_tool_enrollment(self, **kwargs):
        self.kwargs = kwargs
        return "approved"


class TrustedMediaToolStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.ffmpeg = self.root / "ffmpeg"
        self.ffmpeg.write_bytes(b"trusted ffmpeg")
        self.ffmpeg.chmod(0o755)
        self.approver = _Approve()
        self.store = TrustedMediaToolStore(
            path=self.root / "config" / "trusted-media-tools.json",
            staging_root=self.root / "staged",
        )

    def test_enrollment_rejects_symlink_and_group_writable_binary(self) -> None:
        link = self.root / "ffmpeg-link"
        link.symlink_to(self.ffmpeg)
        with self.assertRaises(TrustedMediaToolError):
            self.store.enroll("ffmpeg", link, approval_provider=self.approver)
        self.ffmpeg.chmod(0o775)
        with self.assertRaises(TrustedMediaToolError):
            self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)

    def test_enrollment_rejects_root_owned_binary_for_non_root_user(self) -> None:
        real_stat = self.ffmpeg.stat()
        root_stat = os.stat_result(
            (
                real_stat.st_mode,
                real_stat.st_ino,
                real_stat.st_dev,
                1,
                0,
                real_stat.st_gid,
                real_stat.st_size,
                real_stat.st_atime,
                real_stat.st_mtime,
                real_stat.st_ctime,
            )
        )
        original_stat = Path.stat
        digest = hashlib.sha256(b"trusted ffmpeg").hexdigest()
        with (
            patch("scripts.trusted_media_tools.os.getuid", return_value=501),
            patch("scripts.trusted_media_tools._digest_opened_file", return_value=(digest, root_stat)),
            patch.object(
                Path,
                "stat",
                autospec=True,
                side_effect=lambda path: root_stat if path == self.ffmpeg else original_stat(path),
            ),
        ):
            with self.assertRaises(TrustedMediaToolError):
                self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)

    def test_enrollment_is_approved_and_config_is_private(self) -> None:
        result = self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        digest = hashlib.sha256(b"trusted ffmpeg").hexdigest()
        self.assertEqual(result["sha256"], digest)
        self.assertEqual(
            self.approver.kwargs,
            {
                "kind": "ffmpeg",
                "path": str(self.ffmpeg.resolve()),
                "owner_uid": os.getuid(),
                "sha256": digest,
            },
        )
        self.assertEqual(self.store.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_digest_change_after_enrollment_fails_closed(self) -> None:
        self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        self.ffmpeg.write_bytes(b"changed executable")
        with self.assertRaises(TrustedMediaToolError):
            self.store.load_required({"ffmpeg"})

    def test_load_required_stages_private_executable_copy(self) -> None:
        enrolled = self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        loaded = self.store.load_required({"ffmpeg"})
        tool = loaded["ffmpeg"]
        self.assertEqual(tool.kind, "ffmpeg")
        self.assertEqual(tool.source_path, enrolled["source_path"])
        self.assertEqual(tool.sha256, enrolled["sha256"])
        self.assertNotEqual(tool.staged_path, tool.source_path)
        self.assertEqual(Path(tool.staged_path).read_bytes(), b"trusted ffmpeg")
        self.assertEqual(Path(tool.staged_path).stat().st_mode & 0o777, 0o500)
        self.assertEqual(Path(tool.staged_path).parent.stat().st_mode & 0o777, 0o700)

    def test_unknown_kind_and_unenrolled_required_kind_fail_closed(self) -> None:
        with self.assertRaises(TrustedMediaToolError):
            self.store.enroll("bash", self.ffmpeg, approval_provider=self.approver)
        self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        with self.assertRaises(TrustedMediaToolError):
            self.store.load_required({"ffprobe"})

    def test_load_rejects_overpermissive_or_unexpected_config(self) -> None:
        self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        os.chmod(self.store.path, 0o644)
        with self.assertRaises(TrustedMediaToolError):
            self.store.load_required({"ffmpeg"})
        os.chmod(self.store.path, 0o600)
        payload = json.loads(self.store.path.read_text(encoding="utf-8"))
        payload["unexpected"] = True
        self.store.path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(self.store.path, 0o600)
        with self.assertRaises(TrustedMediaToolError):
            self.store.load_required({"ffmpeg"})


if __name__ == "__main__":
    unittest.main()
