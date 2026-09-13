from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.json_contracts import validate_contract
from scripts.media_intake_service import MediaIntakeError, MediaIntakeService, MediaLimitError
from scripts.video_project_store import VideoProjectStore


class FakeMediaAdapter:
    def __init__(self, *, before_probe=None, duration="12.5", format_name="mov,mp4,m4a,3gp,3g2,mj2") -> None:
        self.before_probe = before_probe
        self.duration = duration
        self.format_name = format_name

    def probe_json(self, path: Path) -> dict[str, object]:
        if self.before_probe:
            self.before_probe()
        return {
            "format": {"duration": self.duration, "format_name": self.format_name},
            "streams": [
                {
                    "codec_type": "video", "codec_name": "h264", "width": 1920,
                    "height": 1080, "avg_frame_rate": "30000/1001",
                },
                {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"},
            ],
        }


class StaticMediaAdapter:
    def __init__(self, payload) -> None:
        self.payload = payload

    def probe_json(self, path: Path) -> dict[str, object]:
        return self.payload


class RecordingApprovalProvider:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def confirm(self, request):
        self.requests.append(dict(request))
        return "native-user-confirmed"


class DenyingApprovalProvider:
    def confirm(self, request):
        raise PermissionError("denied")


class BlockingDenyApprovalProvider:
    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self.entered = entered
        self.release = release

    def confirm(self, request):
        self.entered.set()
        if not self.release.wait(5):
            raise AssertionError("approval interleaving timed out")
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
        self.assertEqual(receipt["version"], "v001")
        persisted_path = self.store.project_root(self.project_id) / "source_receipt" / "v001.json"
        persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
        validate_contract(persisted, "source_receipt.schema.json")
        self.assertEqual(persisted, receipt)

    def test_source_over_2_gib_or_1800_seconds_returns_segment_source_action(self) -> None:
        over_duration = MediaIntakeService(
            self.store, FakeMediaAdapter(duration="1800.01"), self.approval
        )
        with self.assertRaisesRegex(MediaLimitError, "segment_source") as raised:
            over_duration.intake(self.project_id, self.source, [self.approved_root])
        self.assertEqual(raised.exception.action["type"], "segment_source")
        self.assertEqual(self.approval.requests, [])

    def test_duration_cannot_be_overridden_by_caller(self) -> None:
        with self.assertRaises(TypeError):
            self.intake.intake(
                self.project_id,
                self.source,
                [self.approved_root],
                probed_duration_seconds=1.0,
            )

    def test_intake_rejects_probe_type_mismatch(self) -> None:
        self.source.write_bytes(b"not-a-video")
        with self.assertRaises(MediaIntakeError):
            self.intake.intake(self.project_id, self.source, [self.approved_root])

    def test_intake_rejects_incomplete_audio_probe_metadata(self) -> None:
        incomplete = FakeMediaAdapter().probe_json(self.source)
        incomplete["streams"][1].pop("channels")
        service = MediaIntakeService(
            self.store, StaticMediaAdapter(incomplete), self.approval
        )
        with self.assertRaises(MediaIntakeError):
            service.intake(self.project_id, self.source, [self.approved_root])
        self.assertEqual(self.approval.requests, [])

    def test_denied_repeat_intake_does_not_delete_durable_existing_source(self) -> None:
        receipt = self.intake.intake(self.project_id, self.source, [self.approved_root])
        staged = Path(receipt["staged_path"])
        denied = MediaIntakeService(self.store, FakeMediaAdapter(), DenyingApprovalProvider())
        with self.assertRaises(PermissionError):
            denied.intake(self.project_id, self.source, [self.approved_root])
        self.assertTrue(staged.is_file())
        self.assertEqual(staged.read_bytes(), self.source.read_bytes())

    def test_accepts_webm_and_rejects_matroska_or_probe_disagreement(self) -> None:
        webm = self.approved_root / "source.webm"
        webm.write_bytes(b"\x1aE\xdf\xa3\x87\x42\x82\x84webm" + b"x" * 128)
        service = MediaIntakeService(
            self.store, FakeMediaAdapter(format_name="matroska,webm"), self.approval
        )
        receipt = service.intake(self.project_id, webm, [self.approved_root])
        self.assertEqual(receipt["mime_type"], "video/webm")

        matroska = self.approved_root / "source.mkv"
        matroska.write_bytes(b"\x1aE\xdf\xa3\x8b\x42\x82\x88matroska" + b"x" * 128)
        with self.assertRaises(MediaIntakeError):
            service.intake(self.project_id, matroska, [self.approved_root])

        disagreement = MediaIntakeService(
            self.store, FakeMediaAdapter(format_name="matroska,webm"), self.approval
        )
        with self.assertRaises(MediaIntakeError):
            disagreement.intake(self.project_id, self.source, [self.approved_root])

        spoofed = self.approved_root / "spoofed.webm"
        spoofed.write_bytes(b"\x1aE\xdf\xa3\x80payload\x42\x82\x84webm")
        with self.assertRaises(MediaIntakeError):
            service.intake(self.project_id, spoofed, [self.approved_root])

    def test_iso_bmff_probe_agreement_is_exact_unless_probe_reports_combined_family(self) -> None:
        quicktime = self.approved_root / "source.mov"
        quicktime.write_bytes(b"\x00\x00\x00\x18ftypqt  " + b"x" * 128)

        for source, probe_format in ((quicktime, "mp4"), (self.source, "mov")):
            with self.subTest(rejected=(source.name, probe_format)):
                service = MediaIntakeService(
                    self.store,
                    FakeMediaAdapter(format_name=probe_format),
                    self.approval,
                )
                with self.assertRaises(MediaIntakeError):
                    service.intake(self.project_id, source, [self.approved_root])

        for source, probe_format, expected_mime in (
            (quicktime, "mov", "video/quicktime"),
            (self.source, "mp4", "video/mp4"),
        ):
            with self.subTest(matching_single_token=(source.name, probe_format)):
                service = MediaIntakeService(
                    self.store,
                    FakeMediaAdapter(format_name=probe_format),
                    self.approval,
                )
                receipt = service.intake(self.project_id, source, [self.approved_root])
                self.assertEqual(receipt["mime_type"], expected_mime)

        for source in (quicktime, self.source):
            with self.subTest(combined_family=source.name):
                service = MediaIntakeService(
                    self.store,
                    FakeMediaAdapter(format_name="mov,mp4,m4a,3gp,3g2,mj2"),
                    self.approval,
                )
                receipt = service.intake(self.project_id, source, [self.approved_root])
                self.assertEqual(
                    receipt["mime_type"],
                    "video/quicktime" if source == quicktime else "video/mp4",
                )

    def test_staged_path_replacement_after_hash_is_rejected_before_receipt(self) -> None:
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        final = self.store.project_root(self.project_id) / "source" / f"{digest}.mp4"
        original_read = os.read
        approved = False
        replaced = False

        class MarkingApprovalProvider:
            def confirm(inner_self, request):
                nonlocal approved
                approved = True
                return "native-user-confirmed"

        def replacing_read(descriptor: int, count: int) -> bytes:
            nonlocal replaced
            chunk = original_read(descriptor, count)
            if approved and not replaced and chunk == b"":
                replacement = final.with_name("replacement.mp4")
                replacement.write_bytes(b"z" * self.source.stat().st_size)
                os.replace(replacement, final)
                replaced = True
            return chunk

        service = MediaIntakeService(
            self.store,
            FakeMediaAdapter(),
            MarkingApprovalProvider(),
        )
        with patch("scripts.media_intake_service.os.read", side_effect=replacing_read):
            with self.assertRaises(MediaIntakeError):
                service.intake(self.project_id, self.source, [self.approved_root])
        self.assertTrue(replaced)
        receipt_root = self.store.project_root(self.project_id) / "source_receipt"
        self.assertFalse(receipt_root.exists())

    def test_staged_verification_checks_initial_opened_descriptor_size(self) -> None:
        staged = self.base / "staged.mp4"
        staged.write_bytes(self.source.read_bytes())
        digest = hashlib.sha256(staged.read_bytes()).hexdigest()
        actual = staged.stat()
        wrong_opened = SimpleNamespace(
            st_dev=actual.st_dev,
            st_ino=actual.st_ino,
            st_size=actual.st_size + 1,
        )

        with patch(
            "scripts.media_intake_service.os.fstat",
            side_effect=(wrong_opened, actual),
        ):
            with self.assertRaises(MediaIntakeError):
                MediaIntakeService._verify_staged(staged, digest, actual.st_size)

    def test_same_inode_growth_during_probe_is_rejected(self) -> None:
        def append_source() -> None:
            with self.source.open("ab") as handle:
                handle.write(b"growth")

        service = MediaIntakeService(
            self.store, FakeMediaAdapter(before_probe=append_source), self.approval
        )
        with self.assertRaises(MediaIntakeError):
            service.intake(self.project_id, self.source, [self.approved_root])
        self.assertEqual(self.approval.requests, [])

    def test_copy_counts_bytes_and_rejects_growth_above_limit(self) -> None:
        original_read = os.read
        injected = False

        def growing_read(descriptor: int, count: int) -> bytes:
            nonlocal injected
            chunk = original_read(descriptor, count)
            if count == 1024 * 1024 and chunk and not injected:
                injected = True
                with self.source.open("ab") as source:
                    source.write(b"growth")
            return chunk

        service = MediaIntakeService(
            self.store,
            FakeMediaAdapter(),
            self.approval,
            max_source_bytes=self.source.stat().st_size + 2,
        )
        with patch("scripts.media_intake_service.os.read", side_effect=growing_read):
            with self.assertRaisesRegex(MediaLimitError, "segment_source"):
                service.intake(self.project_id, self.source, [self.approved_root])
        self.assertEqual(self.approval.requests, [])

    def test_concurrent_denial_cannot_remove_accepted_identical_source(self) -> None:
        duplicate = self.approved_root / "duplicate.mp4"
        duplicate.write_bytes(self.source.read_bytes())
        entered = threading.Event()
        release = threading.Event()
        denied_service = MediaIntakeService(
            self.store,
            FakeMediaAdapter(),
            BlockingDenyApprovalProvider(entered, release),
        )
        denied_errors: list[BaseException] = []

        def denied_intake() -> None:
            try:
                denied_service.intake(self.project_id, self.source, [self.approved_root])
            except BaseException as exc:
                denied_errors.append(exc)

        thread = threading.Thread(target=denied_intake)
        thread.start()
        self.assertTrue(entered.wait(5))
        accepted = self.intake.intake(self.project_id, duplicate, [self.approved_root])
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(denied_errors), 1)
        receipt_paths = (
            self.store.project_root(self.project_id) / "source_receipt"
        ).glob("v*.json")
        persisted = [json.loads(path.read_text(encoding="utf-8")) for path in receipt_paths]
        self.assertEqual(len(persisted), 1)
        self.assertTrue(Path(accepted["staged_path"]).is_file())
        self.assertTrue(all(Path(item["staged_path"]).is_file() for item in persisted))
        self.assertTrue(all(Path(item["staged_path"]).stat().st_mode & 0o777 == 0o400 for item in persisted))


if __name__ == "__main__":
    unittest.main()
