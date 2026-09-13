from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from scripts.media_intake_service import MediaIntakeError, MediaIntakeService, MediaLimitError
from scripts.video_project_store import VideoProjectStore


class FakeMediaAdapter:
    def __init__(self, *, before_probe=None) -> None:
        self.before_probe = before_probe

    def probe_json(self, path: Path) -> dict[str, object]:
        if self.before_probe:
            self.before_probe()
        return {
            "format": {"duration": "12.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [
                {
                    "codec_type": "video", "codec_name": "h264", "width": 1920,
                    "height": 1080, "avg_frame_rate": "30000/1001",
                },
                {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"},
            ],
        }


class RecordingApprovalProvider:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def confirm(self, request):
        self.requests.append(dict(request))
        return "native-user-confirmed"


class DenyingApprovalProvider:
    def confirm(self, request):
        raise PermissionError("denied")


class MediaIntakeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.approved_root = self.base / "approved"
        self.approved_root.mkdir()
        self.source = self.approved_root / "source.mp4"
        self.source.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 128)
        self.store = VideoProjectStore(self.base / "projects")
        project = self.store.create(
            title="demo", creative_mode="original_redesign", audio_policy="silent"
        )
        self.project_id = project["project_id"]
        self.approval = RecordingApprovalProvider()
        self.intake = MediaIntakeService(self.store, FakeMediaAdapter(), self.approval)

    def test_intake_rejects_relative_symlink_outside_root_and_changed_inode(self) -> None:
        outside = self.base / "outside.mp4"
        outside.write_bytes(self.source.read_bytes())
        symlink = self.approved_root / "link.mp4"
        symlink.symlink_to(outside)
        for source in (Path("relative.mp4"), symlink, outside):
            with self.subTest(source=source), self.assertRaises(MediaIntakeError):
                self.intake.intake(self.project_id, source, [self.approved_root])

        replacement = self.approved_root / "replacement.mp4"
        replacement.write_bytes(self.source.read_bytes())

        def swap_inode() -> None:
            os.replace(replacement, self.source)

        changing = MediaIntakeService(
            self.store, FakeMediaAdapter(before_probe=swap_inode), self.approval
        )
        with self.assertRaises(MediaIntakeError):
            changing.intake(self.project_id, self.source, [self.approved_root])

    def test_intake_records_sha_probe_and_private_staged_path(self) -> None:
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        receipt = self.intake.intake(self.project_id, self.source, [self.approved_root])
        self.assertEqual(receipt["source_sha256"], digest)
        self.assertEqual(receipt["duration_seconds"], 12.5)
        self.assertEqual(receipt["mime_type"], "video/mp4")
        self.assertEqual(receipt["video_codec"], "h264")
        self.assertEqual((receipt["width"], receipt["height"]), (1920, 1080))
        self.assertAlmostEqual(receipt["fps"], 30000 / 1001)
        self.assertEqual(receipt["audio_streams"][0]["codec"], "aac")
        staged = Path(receipt["staged_path"])
        self.assertEqual(staged.stat().st_mode & 0o777, 0o400)
        self.assertEqual(staged.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.approval.requests[0]["source_sha256"], digest)
        self.assertEqual(self.approval.requests[0]["purpose"], "reference-video-processing")

    def test_source_over_2_gib_or_1800_seconds_returns_segment_source_action(self) -> None:
        with self.assertRaisesRegex(MediaLimitError, "segment_source") as raised:
            self.intake.intake(
                self.project_id,
                self.source,
                [self.approved_root],
                probed_duration_seconds=1800.01,
            )
        self.assertEqual(raised.exception.action["type"], "segment_source")
        self.assertEqual(self.approval.requests, [])

    def test_intake_rejects_probe_type_mismatch(self) -> None:
        self.source.write_bytes(b"not-a-video")
        with self.assertRaises(MediaIntakeError):
            self.intake.intake(self.project_id, self.source, [self.approved_root])

    def test_denied_repeat_intake_does_not_delete_durable_existing_source(self) -> None:
        receipt = self.intake.intake(self.project_id, self.source, [self.approved_root])
        staged = Path(receipt["staged_path"])
        denied = MediaIntakeService(self.store, FakeMediaAdapter(), DenyingApprovalProvider())
        with self.assertRaises(PermissionError):
            denied.intake(self.project_id, self.source, [self.approved_root])
        self.assertTrue(staged.is_file())
        self.assertEqual(staged.read_bytes(), self.source.read_bytes())


if __name__ == "__main__":
    unittest.main()
