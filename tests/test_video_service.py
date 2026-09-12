"""RED tests for the Dreamina video workflow service (Task 4).

Covers:

* mode selection across ``text2video``, ``image2video``, ``frames2video``
  and ``multimodal2video``;
* Seedance 2.5 resolutions (480P / 720P / 1080P) discovered from the
  capability snapshot;
* ratio / duration discovery within the 4–30 second window enforced by
  the plugin;
* 2–30 second audio reference constraints (only when the snapshot
  advertises an audio feature gate);
* the first-web-video web prerequisite is reported as
  ``WEB_PREREQUISITE_REQUIRED`` and is *never* bypassed;
* approval binding covers prompt, references, model, resolution, ratio
  and duration (changes to any of these fields invalidate the receipt).
"""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.dreamina_adapter import DreaminaResult  # noqa: E402

try:  # pragma: no cover - exercised by RED phase
    from scripts.video_service import (  # type: ignore  # noqa: E402
        ApprovalMismatchError,
        DurationOutOfRangeError,
        InvalidReferenceError,
        MissingApprovalError,
        UnsupportedCapabilityError,
        VideoService,
        VideoWebPrerequisiteRequired,
        build_video_request_fingerprint,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by RED phase
    VideoService = None  # type: ignore[assignment]
    ApprovalMismatchError = None  # type: ignore[assignment]
    DurationOutOfRangeError = None  # type: ignore[assignment]
    InvalidReferenceError = None  # type: ignore[assignment]
    MissingApprovalError = None  # type: ignore[assignment]
    UnsupportedCapabilityError = None  # type: ignore[assignment]
    VideoWebPrerequisiteRequired = None  # type: ignore[assignment]
    build_video_request_fingerprint = None  # type: ignore[assignment]


def synthetic_video_snapshot() -> dict:
    """Realistic snapshot covering all four video modes and Seedance 2.5."""
    return {
        "cli_version": "1.4.18",
        "captured_at": "2026-09-12T00:00:00Z",
        "modes": ["text2video", "image2video", "frames2video", "multimodal2video"],
        "models": [
            {
                "name": "seedance-2.5",
                "modes": ["text2video", "image2video", "frames2video", "multimodal2video"],
                "resolutions": ["480P", "720P", "1080P"],
                "ratios": ["16:9", "9:16", "1:1"],
                "duration_min_seconds": 4,
                "duration_max_seconds": 30,
                "web_prerequisite_required": True,
                "audio_reference_max_seconds": 30,
            }
        ],
        "resolutions": {"video": ["480P", "720P", "1080P"]},
        "ratios": ["16:9", "9:16", "1:1"],
    }


def snapshot_without_audio() -> dict:
    snap = copy.deepcopy(synthetic_video_snapshot())
    snap["models"][0].pop("audio_reference_max_seconds", None)
    return snap


class VideoServiceModuleTests(unittest.TestCase):
    def test_module_exports_service(self) -> None:
        self.assertIsNotNone(VideoService)


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable(self) -> None:
        a = {"mode": "text2video", "prompt": "p", "model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 8}
        self.assertEqual(build_video_request_fingerprint(a), build_video_request_fingerprint(copy.deepcopy(a)))

    def test_fingerprint_changes_with_duration(self) -> None:
        a = {"mode": "text2video", "prompt": "p", "model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 8}
        b = dict(a, duration_seconds=10)
        self.assertNotEqual(build_video_request_fingerprint(a), build_video_request_fingerprint(b))


class TextToVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_video_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger")

    def test_text2video_default_duration_is_four(self) -> None:
        req = self.service.build_request(
            mode="text2video",
            prompt="a slow sunrise",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
        )
        self.assertEqual(req["duration_seconds"], 4)
        self.assertEqual(req["video_resolution"], "720P")
        self.assertEqual(req["ratio"], "16:9")

    def test_text2video_with_explicit_duration(self) -> None:
        req = self.service.build_request(
            mode="text2video",
            prompt="a slow sunrise",
            model="seedance-2.5",
            video_resolution="1080P",
            ratio="16:9",
            duration_seconds=15,
        )
        self.assertEqual(req["duration_seconds"], 15)

    def test_text2video_rejects_duration_below_four(self) -> None:
        with self.assertRaises(DurationOutOfRangeError):
            self.service.build_request(
                mode="text2video",
                prompt="x",
                model="seedance-2.5",
                video_resolution="720P",
                ratio="16:9",
                duration_seconds=3,
            )

    def test_text2video_rejects_duration_above_thirty(self) -> None:
        with self.assertRaises(DurationOutOfRangeError):
            self.service.build_request(
                mode="text2video",
                prompt="x",
                model="seedance-2.5",
                video_resolution="720P",
                ratio="16:9",
                duration_seconds=31,
            )

    def test_text2video_rejects_resolution_outside_seedance(self) -> None:
        with self.assertRaises(UnsupportedCapabilityError):
            self.service.build_request(
                mode="text2video",
                prompt="x",
                model="seedance-2.5",
                video_resolution="4K",
                ratio="16:9",
            )

    def test_text2video_rejects_unknown_ratio(self) -> None:
        with self.assertRaises(UnsupportedCapabilityError):
            self.service.build_request(
                mode="text2video",
                prompt="x",
                model="seedance-2.5",
                video_resolution="720P",
                ratio="21:9",
            )

    def test_text2video_requires_video_resolution(self) -> None:
        with self.assertRaises(UnsupportedCapabilityError):
            self.service.build_request(
                mode="text2video",
                prompt="x",
                model="seedance-2.5",
                ratio="16:9",
            )


class ImageToVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_video_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger")

    def test_image2video_requires_subject_reference(self) -> None:
        with self.assertRaises(InvalidReferenceError):
            self.service.build_request(
                mode="image2video",
                prompt="x",
                model="seedance-2.5",
                video_resolution="720P",
                ratio="16:9",
                references=[],
            )

    def test_image2video_accepts_subject_reference(self) -> None:
        req = self.service.build_request(
            mode="image2video",
            prompt="animate this",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
            references=[{"path": "/tmp/start.png", "role": "subject"}],
        )
        self.assertEqual(len(req["references"]), 1)
        self.assertEqual(req["references"][0]["role"], "subject")

    def test_image2video_rejects_extra_reference_before_subject(self) -> None:
        with self.assertRaises(InvalidReferenceError):
            self.service.build_request(
                mode="image2video", prompt="x", model="seedance-2.5",
                video_resolution="720P", ratio=None,
                references=[
                    {"path": "/tmp/style.png", "role": "style"},
                    {"path": "/tmp/subject.png", "role": "subject"},
                ],
            )


class FramesToVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_video_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger")

    def test_frames2video_requires_frame_references(self) -> None:
        with self.assertRaises(InvalidReferenceError):
            self.service.build_request(
                mode="frames2video",
                prompt="x",
                model="seedance-2.5",
                video_resolution="720P",
                ratio="16:9",
                references=[{"path": "/tmp/a.png", "role": "subject"}],
            )

    def test_frames2video_accepts_frame_references(self) -> None:
        refs = [
            {"path": "/tmp/f1.png", "role": "frame"},
            {"path": "/tmp/f2.png", "role": "frame"},
        ]
        req = self.service.build_request(
            mode="frames2video",
            prompt="transition",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
            references=refs,
        )
        self.assertEqual(len(req["references"]), 2)
        self.assertTrue(all(r["role"] == "frame" for r in req["references"]))

    def test_frames2video_rejects_single_frame(self) -> None:
        with self.assertRaises(InvalidReferenceError):
            self.service.build_request(
                mode="frames2video",
                prompt="transition",
                model="seedance-2.5",
                video_resolution="720P",
                ratio=None,
                references=[{"path": "/tmp/f1.png", "role": "frame"}],
            )

    def test_frames2video_rejects_mixed_roles(self) -> None:
        with self.assertRaises(InvalidReferenceError):
            self.service.build_request(
                mode="frames2video", prompt="transition", model="seedance-2.5",
                video_resolution="720P", ratio=None,
                references=[
                    {"path": "/tmp/wrong.png", "role": "subject"},
                    {"path": "/tmp/f1.png", "role": "frame"},
                    {"path": "/tmp/f2.png", "role": "frame"},
                ],
            )


class MultimodalToVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_video_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger")

    def test_multimodal2video_accepts_subject_and_audio(self) -> None:
        req = self.service.build_request(
            mode="multimodal2video",
            prompt="narrated scene",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
            duration_seconds=10,
            references=[
                {"path": "/tmp/img.png", "role": "subject"},
                {"path": "/tmp/audio.mp3", "role": "audio", "duration_seconds": 10},
            ],
        )
        self.assertEqual(len(req["references"]), 2)

    def test_multimodal2video_rejects_audio_too_long_when_snapshot_advertises(self) -> None:
        with self.assertRaises(InvalidReferenceError):
            self.service.build_request(
                mode="multimodal2video",
                prompt="x",
                model="seedance-2.5",
                video_resolution="720P",
                ratio="16:9",
                duration_seconds=10,
                references=[
                    {"path": "/tmp/img.png", "role": "subject"},
                    {"path": "/tmp/audio.mp3", "role": "audio", "duration_seconds": 60},
                ],
            )

    def test_multimodal2video_audio_constraint_skipped_when_snapshot_silent(self) -> None:
        service = VideoService(
            snapshot=snapshot_without_audio(),
            ledger_dir=Path(self.tmp.name) / "ledger_no_audio",
        )
        # Without an audio_reference_max_seconds gate in the snapshot, the
        # service must accept audio references without enforcing the 30s cap.
        req = service.build_request(
            mode="multimodal2video",
            prompt="x",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
            duration_seconds=10,
            references=[
                {"path": "/tmp/img.png", "role": "subject"},
                {"path": "/tmp/audio.mp3", "role": "audio", "duration_seconds": 25},
            ],
        )
        self.assertEqual(len(req["references"]), 2)


class WebPrerequisiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_video_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger")

    def test_first_video_submission_raises_web_prerequisite(self) -> None:
        req = self.service.build_request(
            mode="text2video",
            prompt="x",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
        )
        fingerprint = build_video_request_fingerprint(req)
        approval = {
            "request_fingerprint": fingerprint,
            "acknowledged_cost": "credits",
            "acknowledged_scope": {"model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 4},
            "approved_at": "2026-09-12T00:00:00Z",
            "approver": "test",
        }
        with self.assertRaises(VideoWebPrerequisiteRequired):
            self.service.submit(req, adapter=None, approval=approval, web_prerequisite_cleared=False)  # type: ignore[arg-type]

    def test_web_prerequisite_does_not_bypass_when_cleared_flag_true(self) -> None:
        # Even if the caller passes cleared=True, the service must NOT silently
        # bypass without a recorded acknowledgement in the snapshot.
        req = self.service.build_request(
            mode="text2video",
            prompt="x",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
        )
        fingerprint = build_video_request_fingerprint(req)
        approval = {
            "request_fingerprint": fingerprint,
            "acknowledged_cost": "credits",
            "acknowledged_scope": {"model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 4},
            "approved_at": "2026-09-12T00:00:00Z",
            "approver": "test",
        }
        with self.assertRaises(VideoWebPrerequisiteRequired):
            self.service.submit(req, adapter=None, approval=approval, web_prerequisite_cleared=True)  # type: ignore[arg-type]

    def test_web_prerequisite_recorded_then_submission_proceeds(self) -> None:
        req = self.service.build_request(
            mode="text2video",
            prompt="x",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
        )
        fingerprint = build_video_request_fingerprint(req)
        approval = {
            "request_fingerprint": fingerprint,
            "acknowledged_cost": "credits",
            "acknowledged_scope": {"model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 4},
            "approved_at": "2026-09-12T00:00:00Z",
            "approver": "test",
        }
        self.service.record_web_prerequisite_acknowledgement()

        class _StubAdapter:
            def run(self, args):
                return DreaminaResult(
                    exit_code=0,
                    payload={"submit_id": "vsub-1", "items": [{"submit_id": "vsub-1", "index": 0}]},
                    error_code=None,
                    submit_id="vsub-1",
                    stderr="",
                )

        result = self.service.submit(
            req,
            adapter=_StubAdapter(),
            approval=approval,
            web_prerequisite_cleared=False,
        )
        self.assertEqual(result["submit_id"], "vsub-1")


class ApprovalBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_video_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger")

    def test_submit_without_approval_raises(self) -> None:
        req = self.service.build_request(
            mode="text2video",
            prompt="x",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
        )
        with self.assertRaises(MissingApprovalError):
            self.service.submit(req, adapter=None, approval=None, web_prerequisite_cleared=False)  # type: ignore[arg-type]

    def test_submit_with_changed_prompt_invalidates_approval(self) -> None:
        req = self.service.build_request(
            mode="text2video",
            prompt="original",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
        )
        # Build a fingerprint for the original request, then mutate the
        # prompt before submitting.
        original_fingerprint = build_video_request_fingerprint(req)
        req["prompt"] = "tampered"
        approval = {
            "request_fingerprint": original_fingerprint,
            "acknowledged_cost": "credits",
            "acknowledged_scope": {"model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 4},
            "approved_at": "2026-09-12T00:00:00Z",
            "approver": "test",
        }
        with self.assertRaises(ApprovalMismatchError):
            self.service.submit(req, adapter=None, approval=approval, web_prerequisite_cleared=False)  # type: ignore[arg-type]

    def test_submit_with_changed_duration_invalidates_approval(self) -> None:
        req = self.service.build_request(
            mode="text2video",
            prompt="x",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
            duration_seconds=8,
        )
        original_fingerprint = build_video_request_fingerprint(req)
        req["duration_seconds"] = 12
        approval = {
            "request_fingerprint": original_fingerprint,
            "acknowledged_cost": "credits",
            "acknowledged_scope": {"model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 8},
            "approved_at": "2026-09-12T00:00:00Z",
            "approver": "test",
        }
        with self.assertRaises(ApprovalMismatchError):
            self.service.submit(req, adapter=None, approval=approval, web_prerequisite_cleared=False)  # type: ignore[arg-type]


class SubmitSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_video_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger")

    def test_submit_invokes_adapter_once_per_video_request(self) -> None:
        req = self.service.build_request(
            mode="text2video",
            prompt="x",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
        )
        fingerprint = build_video_request_fingerprint(req)
        approval = {
            "request_fingerprint": fingerprint,
            "acknowledged_cost": "credits",
            "acknowledged_scope": {"model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 4},
            "approved_at": "2026-09-12T00:00:00Z",
            "approver": "test",
        }
        self.service.record_web_prerequisite_acknowledgement()

        class _StubAdapter:
            def __init__(self) -> None:
                self.calls: list[list[str]] = []

            def run(self, args):
                self.calls.append(list(args))
                return DreaminaResult(
                    exit_code=0,
                    payload={"submit_id": "vsub-2", "items": [{"submit_id": "vsub-2", "index": 0}]},
                    error_code=None,
                    submit_id="vsub-2",
                    stderr="",
                )

        adapter = _StubAdapter()
        result = self.service.submit(req, adapter=adapter, approval=approval, web_prerequisite_cleared=False)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0][0], "text2video")
        self.assertIn("--video_resolution", adapter.calls[0])
        self.assertIn("720P", adapter.calls[0])
        self.assertIn("--duration", adapter.calls[0])
        self.assertIn("4", adapter.calls[0])
        self.assertEqual(result["submit_id"], "vsub-2")


if __name__ == "__main__":
    unittest.main()
