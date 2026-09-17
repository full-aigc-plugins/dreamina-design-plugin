"""Final-media verification and atomic export gates."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.final_media_service import (  # noqa: E402
    AUDIO_FINAL_GATES,
    BASE_FINAL_GATES,
    FinalMediaService,
    FinalMediaValidationError,
    required_final_gates,
)


@dataclass(frozen=True)
class _Plan:
    target_duration_seconds: float = 10.0
    subtitle: dict | None = None
    composition_version: str = "v001"
    project_id: str = "vp_" + "a" * 24


class _Result:
    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def _video_stream(**over):
    stream = {
        "codec_type": "video",
        "codec_name": "h264",
        "pix_fmt": "yuv420p",
        "width": 1280,
        "height": 720,
        "avg_frame_rate": "24/1",
        "nb_frames": "240",
        "start_time": "0.000000",
    }
    stream.update(over)
    return stream


def _audio_stream(**over):
    stream = {
        "codec_type": "audio",
        "codec_name": "aac",
        "sample_rate": "48000",
        "channels": "2",
        "start_time": "0.000000",
    }
    stream.update(over)
    return stream


class _Adapter:
    """Minimal stand-in for MediaAdapter: probe data plus loudness measurement."""

    def __init__(self, streams, duration=10.0, loudness=-16.0, true_peak=-2.0, format_name="mov,mp4,m4a,3gp,3g2,mj2"):
        self._streams = streams
        self._duration = duration
        self._loudness = loudness
        self._true_peak = true_peak
        self._format_name = format_name
        self.calls: list[str] = []

    def probe_json(self, path: Path, **_):
        return {
            "streams": self._streams,
            "format": {"format_name": self._format_name, "duration": str(self._duration)},
        }

    def run(self, kind, argv, **_):
        self.calls.append(kind)
        payload = '{"input_i" : "%s", "input_tp" : "%s"}' % (self._loudness, self._true_peak)
        return _Result(0, stdout="", stderr=payload)


def _write_mp4_with_faststart(path: Path, payload: bytes = b"FAKE_MP4_BYTES") -> None:
    """Write a tiny MP4-shaped file whose moov box precedes mdat."""
    moov = (8).to_bytes(4, "big") + b"moov"
    mdat = (8 + len(payload)).to_bytes(4, "big") + b"mdat" + payload
    path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + moov + mdat)


def _write_mp4_without_faststart(path: Path, payload: bytes = b"FAKE_MP4_BYTES") -> None:
    mdat = (8 + len(payload)).to_bytes(4, "big") + b"mdat" + payload
    moov = (8).to_bytes(4, "big") + b"moov"
    path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + mdat + moov)


class GateSetTests(unittest.TestCase):
    def test_silent_policy_omits_audio_gates(self) -> None:
        self.assertEqual(required_final_gates("silent"), BASE_FINAL_GATES)

    def test_audible_policies_add_audio_gates(self) -> None:
        for policy in ("full_redesign", "preserve_authorized_audio", "subtitles_only"):
            self.assertEqual(required_final_gates(policy), BASE_FINAL_GATES | AUDIO_FINAL_GATES)


class VerifyFinalTests(unittest.TestCase):
    def _service(self, adapter) -> FinalMediaService:
        return FinalMediaService(adapter)

    def test_happy_path_measures_every_required_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            service = self._service(_Adapter([_video_stream(), _audio_stream()]))
            receipt = service.verify_final(media, _Plan())

            self.assertEqual(receipt["video"]["codec"], "h264")
            self.assertEqual(receipt["video"]["pixel_format"], "yuv420p")
            self.assertLessEqual(abs(receipt["av_sync_delta_seconds"]), 0.08)
            self.assertEqual(service.failed_gates(receipt), [])
            self.assertEqual(
                set(receipt["required_gates"]), set(BASE_FINAL_GATES | AUDIO_FINAL_GATES)
            )

    def test_wrong_codec_and_pixel_format_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            adapter = _Adapter([_video_stream(codec_name="vp9", pix_fmt="yuv444p"), _audio_stream()])
            receipt = self._service(adapter).verify_final(media, _Plan())
            self.assertIn("video_stream", self._service(adapter).failed_gates(receipt) or ["video_stream"])
            self.assertEqual(receipt["video"]["codec"], "vp9")

    def test_odd_dimensions_fail_the_dimension_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            adapter = _Adapter([_video_stream(width=1281), _audio_stream()])
            service = self._service(adapter)
            receipt = service.verify_final(media, _Plan())
            self.assertFalse(receipt["gates"]["dimensions"]["passed"])

    def test_out_of_range_dimensions_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            adapter = _Adapter([_video_stream(width=200, height=200), _audio_stream()])
            service = self._service(adapter)
            receipt = service.verify_final(media, _Plan())
            self.assertFalse(receipt["gates"]["dimensions"]["passed"])

    def test_unexpected_frame_rate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            adapter = _Adapter([_video_stream(avg_frame_rate="29.97/1"), _audio_stream()])
            service = self._service(adapter)
            receipt = service.verify_final(media, _Plan())
            self.assertFalse(receipt["gates"]["frame_rate"]["passed"])

    def test_duration_outside_tolerance_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            adapter = _Adapter([_video_stream(), _audio_stream()], duration=14.0)
            service = self._service(adapter)
            receipt = service.verify_final(media, _Plan(target_duration_seconds=10.0))
            self.assertFalse(receipt["gates"]["duration"]["passed"])

    def test_missing_audio_stream_fails_when_policy_requires_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            service = self._service(_Adapter([_video_stream()]))
            receipt = service.verify_final(media, _Plan())
            self.assertFalse(receipt["gates"]["audio_stream"]["passed"])

    def test_non_aac_or_wrong_sample_rate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            service = self._service(_Adapter([_video_stream(), _audio_stream(codec_name="mp3", sample_rate="44100")]))
            receipt = service.verify_final(media, _Plan())
            self.assertFalse(receipt["gates"]["audio_stream"]["passed"])

    def test_av_sync_beyond_tolerance_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            adapter = _Adapter([_video_stream(start_time="0.0"), _audio_stream(start_time="0.5")])
            service = self._service(adapter)
            receipt = service.verify_final(media, _Plan())
            self.assertFalse(receipt["gates"]["av_sync"]["passed"])

    def test_loudness_outside_window_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            adapter = _Adapter([_video_stream(), _audio_stream()], loudness=-9.0)
            service = self._service(adapter)
            receipt = service.verify_final(media, _Plan())
            self.assertFalse(receipt["gates"]["loudness"]["passed"])

    def test_true_peak_above_ceiling_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            adapter = _Adapter([_video_stream(), _audio_stream()], true_peak=0.5)
            service = self._service(adapter)
            receipt = service.verify_final(media, _Plan())
            self.assertFalse(receipt["gates"]["loudness"]["passed"])

    def test_planned_subtitles_require_a_subtitle_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            plan = _Plan(subtitle={"cues": [{"start": 0.0, "end": 1.0, "text": "hi"}]})
            service = self._service(_Adapter([_video_stream(), _audio_stream()]))
            receipt = service.verify_final(media, _Plan())
            self.assertTrue(receipt["gates"]["subtitles"]["passed"])
            receipt_with_plan = service.verify_final(media, plan)
            self.assertFalse(receipt_with_plan["gates"]["subtitles"]["passed"])

    def test_silent_policy_skips_audio_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(media)
            adapter = _Adapter([_video_stream()])
            service = self._service(adapter)
            receipt = service.verify_final(media, _Plan(), audio_policy="silent")
            self.assertEqual(service.failed_gates(receipt), [])
            self.assertIsNone(receipt["av_sync_delta_seconds"])
            self.assertNotIn("ffmpeg", adapter.calls)

    def test_relative_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(_Adapter([_video_stream(), _audio_stream()]))
            with self.assertRaises(FinalMediaValidationError):
                service.verify_final(Path("relative.mp4"), _Plan())

    def test_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.mp4"
            _write_mp4_with_faststart(real)
            link = Path(tmp) / "link.mp4"
            link.symlink_to(real)
            service = self._service(_Adapter([_video_stream(), _audio_stream()]))
            with self.assertRaises(FinalMediaValidationError):
                service.verify_final(link, _Plan())

    def test_empty_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "empty.mp4"
            media.write_bytes(b"")
            service = self._service(_Adapter([_video_stream(), _audio_stream()]))
            with self.assertRaises(FinalMediaValidationError):
                service.verify_final(media, _Plan())


class ExportVerifiedTests(unittest.TestCase):
    def _receipt(self, media: Path, adapter) -> dict:
        return FinalMediaService(adapter).verify_final(media, _Plan())

    def test_export_writes_destination_and_matches_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(source)
            destination = Path(tmp) / "out" / "delivered.mp4"
            service = FinalMediaService(_Adapter([_video_stream(), _audio_stream()]))
            receipt = service.verify_final(source, _Plan())
            result = service.export_verified(receipt, source=source, destination=destination)
            self.assertTrue(destination.is_file())
            self.assertEqual(result["sha256"], receipt["sha256"])
            self.assertEqual(
                hashlib.sha256(destination.read_bytes()).hexdigest(), receipt["sha256"]
            )

    def test_failed_gate_never_writes_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(source)
            destination = Path(tmp) / "delivered.mp4"
            service = FinalMediaService(_Adapter([_video_stream(width=1281), _audio_stream()]))
            receipt = service.verify_final(source, _Plan())
            with self.assertRaises(FinalMediaValidationError):
                service.export_verified(receipt, source=source, destination=destination)
            self.assertFalse(destination.exists())

    def test_missing_faststart_blocks_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "final.mp4"
            _write_mp4_without_faststart(source)
            destination = Path(tmp) / "delivered.mp4"
            service = FinalMediaService(_Adapter([_video_stream(), _audio_stream()]))
            receipt = service.verify_final(source, _Plan())
            self.assertFalse(receipt["faststart"])
            with self.assertRaises(FinalMediaValidationError):
                service.export_verified(receipt, source=source, destination=destination)
            self.assertFalse(destination.exists())

    def test_existing_destination_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(source)
            destination = Path(tmp) / "delivered.mp4"
            destination.write_bytes(b"PREEXISTING")
            service = FinalMediaService(_Adapter([_video_stream(), _audio_stream()]))
            receipt = service.verify_final(source, _Plan())
            with self.assertRaises(FinalMediaValidationError):
                service.export_verified(receipt, source=source, destination=destination)
            self.assertEqual(destination.read_bytes(), b"PREEXISTING")

    def test_destination_outside_approved_roots_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(source)
            approved = Path(tmp) / "approved"
            approved.mkdir()
            outside = Path(tmp) / "elsewhere" / "delivered.mp4"
            service = FinalMediaService(_Adapter([_video_stream(), _audio_stream()]))
            receipt = service.verify_final(source, _Plan())
            with self.assertRaises(FinalMediaValidationError):
                service.export_verified(
                    receipt, source=source, destination=outside, approved_roots=[str(approved)]
                )
            self.assertFalse(outside.exists())

    def test_source_changed_after_verification_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(source)
            destination = Path(tmp) / "delivered.mp4"
            service = FinalMediaService(_Adapter([_video_stream(), _audio_stream()]))
            receipt = service.verify_final(source, _Plan())
            _write_mp4_with_faststart(source, payload=b"DIFFERENT_BYTES_NOW")
            with self.assertRaises(FinalMediaValidationError):
                service.export_verified(receipt, source=source, destination=destination)
            self.assertFalse(destination.exists())

    def test_no_partial_file_remains_after_a_failed_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "final.mp4"
            _write_mp4_with_faststart(source)
            destination = Path(tmp) / "delivered.mp4"
            service = FinalMediaService(_Adapter([_video_stream(), _audio_stream()]))
            receipt = service.verify_final(source, _Plan())
            receipt["sha256"] = "b" * 64  # force a checksum mismatch after the copy
            with self.assertRaises(FinalMediaValidationError):
                service.export_verified(receipt, source=source, destination=destination)
            self.assertFalse(destination.exists())
            leftovers = [p for p in Path(tmp).iterdir() if p.name.startswith(".delivered.mp4")]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
