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
        self.assertEqual(result["owner_uid"], os.getuid())
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

    def test_recorded_owner_uid_mismatch_fails_closed(self) -> None:
        self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        payload = json.loads(self.store.path.read_text(encoding="utf-8"))
        payload["tools"]["ffmpeg"]["owner_uid"] = os.getuid() + 1
        self.store.path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(self.store.path, 0o600)
        with self.assertRaisesRegex(TrustedMediaToolError, "owner"):
            self.store.load_required({"ffmpeg"})

    def test_first_enrollment_rejects_symlinked_config_directory(self) -> None:
        target = self.root / "config-target"
        target.mkdir(mode=0o700)
        self.store.path.parent.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(TrustedMediaToolError, "directory"):
            self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        self.assertFalse((target / self.store.path.name).exists())

    def test_first_enrollment_rejects_overpermissive_config_directory(self) -> None:
        self.store.path.parent.mkdir(mode=0o755)
        with self.assertRaisesRegex(TrustedMediaToolError, "directory"):
            self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        self.assertFalse(self.store.path.exists())

    def test_first_enrollment_rejects_foreign_owned_config_directory(self) -> None:
        self.store.path.parent.mkdir(mode=0o700)
        original_lstat = Path.lstat
        real_stat = self.store.path.parent.lstat()
        foreign_stat = os.stat_result(
            (
                real_stat.st_mode,
                real_stat.st_ino,
                real_stat.st_dev,
                real_stat.st_nlink,
                os.getuid() + 1,
                real_stat.st_gid,
                real_stat.st_size,
                real_stat.st_atime,
                real_stat.st_mtime,
                real_stat.st_ctime,
            )
        )
        with patch.object(
            Path,
            "lstat",
            autospec=True,
            side_effect=lambda path: (
                foreign_stat if path == self.store.path.parent else original_lstat(path)
            ),
        ):
            with self.assertRaisesRegex(TrustedMediaToolError, "directory"):
                self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        self.assertFalse(self.store.path.exists())

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

    def test_load_requires_exact_config_file_mode_0600(self) -> None:
        self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        for mode in (0o400, 0o500, 0o700, 0o640, 0o644):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.store.path, mode)
                try:
                    with self.assertRaisesRegex(TrustedMediaToolError, "0600"):
                        self.store.load_required({"ffmpeg"})
                finally:
                    os.chmod(self.store.path, 0o600)

    def test_load_requires_exact_config_directory_mode_0700(self) -> None:
        self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)
        for mode in (0o500, 0o600, 0o750, 0o755):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.store.path.parent, mode)
                try:
                    with self.assertRaisesRegex(TrustedMediaToolError, "0700"):
                        self.store.load_required({"ffmpeg"})
                finally:
                    os.chmod(self.store.path.parent, 0o700)
        os.chmod(self.store.path, 0o600)
        payload = json.loads(self.store.path.read_text(encoding="utf-8"))
        payload["unexpected"] = True
        self.store.path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(self.store.path, 0o600)
        with self.assertRaises(TrustedMediaToolError):
            self.store.load_required({"ffmpeg"})

    def test_temp_open_failure_closes_directory_fd_without_mutation(self) -> None:
        real_open = os.open
        opened_directory_fds: list[int] = []

        def fail_temp_open(path, flags, mode=0o777, *, dir_fd=None):
            if dir_fd is not None:
                raise OSError("injected temp open failure")
            descriptor = real_open(path, flags, mode)
            if Path(path) == self.store.path.parent:
                opened_directory_fds.append(descriptor)
            return descriptor

        with (
            patch("scripts.trusted_media_tools.os.open", side_effect=fail_temp_open),
            patch("scripts.trusted_media_tools.os.replace") as replace,
            patch("scripts.trusted_media_tools.os.unlink") as unlink,
        ):
            with self.assertRaisesRegex(OSError, "injected temp open failure"):
                self.store.enroll("ffmpeg", self.ffmpeg, approval_provider=self.approver)

        self.assertEqual(len(opened_directory_fds), 1)
        directory_fd = opened_directory_fds[0]
        try:
            with self.assertRaises(OSError):
                os.fstat(directory_fd)
        finally:
            try:
                os.close(directory_fd)
            except OSError:
                pass
        replace.assert_not_called()
        unlink.assert_not_called()

    def test_raw_temp_close_failure_still_closes_directory_fd_without_mutation(self) -> None:
        real_open = os.open
        real_close = os.close
        opened_directory_fds: list[int] = []
        opened_temp_fds: list[int] = []

        def record_open(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if dir_fd is None and Path(path) == self.store.path.parent:
                opened_directory_fds.append(descriptor)
            elif dir_fd is not None:
                opened_temp_fds.append(descriptor)
            return descriptor

        def close_then_fail_raw_temp(descriptor):
            real_close(descriptor)
            if descriptor in opened_temp_fds:
                raise OSError("injected raw temp close failure")

        with (
            patch("scripts.trusted_media_tools.os.open", side_effect=record_open),
            patch("scripts.trusted_media_tools.os.fchmod", side_effect=OSError("before fdopen")),
            patch("scripts.trusted_media_tools.os.close", side_effect=close_then_fail_raw_temp),
            patch("scripts.trusted_media_tools.os.replace") as replace,
        ):
            with self.assertRaisesRegex(OSError, "injected raw temp close failure"):
                self.store._atomic_write({"version": 1, "tools": {}})

        self.assertEqual(len(opened_directory_fds), 1)
        self.assertEqual(len(opened_temp_fds), 1)
        with self.assertRaises(OSError):
            os.fstat(opened_directory_fds[0])
        replace.assert_not_called()
        self.assertFalse(self.store.path.exists())
        self.assertEqual(list(self.store.path.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
