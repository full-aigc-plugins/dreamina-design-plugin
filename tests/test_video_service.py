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
from scripts.approval_guard import ApprovalGuard  # noqa: E402
from scripts.json_contracts import canonical_fingerprint  # noqa: E402


class _TestReferencePolicy:
    def validate(self, reference):
        return dict(reference)

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
        "modes": ["text2video", "image2video", "frames2video", "multiframe2video", "multimodal2video"],
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
        "mode_limits": {"multiframe2video": {"min_references": 2, "max_references": 20, "request_duration_min_seconds": 2, "request_duration_max_seconds": 30, "transition_duration_min_seconds": 1, "transition_duration_max_seconds": 8}},
    }


def snapshot_without_audio() -> dict:
    snap = copy.deepcopy(synthetic_video_snapshot())
    snap["models"][0].pop("audio_reference_max_seconds", None)
    return snap


def issue_approval(root: Path, request: dict, scope: dict):
    scope = {
        "count": 1,
        "model": request.get("model"),
        "resolution": request.get("video_resolution"),
        "ratio": request.get("ratio"),
        "duration_seconds": request.get("duration_seconds"),
        **scope,
    }
    scope = {key: value for key, value in scope.items() if value is not None}
    guard = ApprovalGuard(root=root)
    session_id = guard.create_session(label="submission")
    approval_id = guard.record_approval(
        session_id,
        request=request,
        receipt={
            "request_fingerprint": build_video_request_fingerprint(request),
            "acknowledged_cost": "credits",
            "acknowledged_scope": scope,
            "approved_at": "2026-09-12T00:00:00Z",
            "approver": "test",
        },
        fingerprint_builder=build_video_request_fingerprint,
    )
    return guard, session_id, approval_id


def issue_receipt(root: Path, request: dict, receipt: dict):
    receipt = dict(receipt)
    raw_scope = dict(receipt["acknowledged_scope"])
    raw_scope.pop("video_resolution", None)
    normalized_scope = {
        "count": 1,
        "model": request.get("model"),
        "resolution": request.get("video_resolution"),
        "ratio": request.get("ratio"),
        "duration_seconds": request.get("duration_seconds"),
        **raw_scope,
    }
    receipt["acknowledged_scope"] = {
        key: value for key, value in normalized_scope.items() if value is not None
    }
    guard = ApprovalGuard(root=root)
    session_id = guard.create_session(label="submission")
    approval_id = guard.record_approval(session_id, request=request, receipt=receipt)
    return guard, session_id, approval_id


def approval_args(root: Path, request: dict, receipt: dict) -> dict:
    guard, session_id, approval_id = issue_receipt(root, request, receipt)
    return {
        "approval_guard": guard,
        "session_id": session_id,
        "approval_id": approval_id,
    }


class VideoServiceModuleTests(unittest.TestCase):
    def test_module_exports_service(self) -> None:
        self.assertIsNotNone(VideoService)

    def test_batch_allowance_reserves_exact_request_and_commits_submit_id(self) -> None:
        request = {"mode": "text2video", "prompt": "p", "model": "seedance-test", "video_resolution": "720p", "duration_seconds": 4}
        calls = []
        class Allowance:
            def reserve(self, allowance_id, **fields):
                calls.append(("reserve", allowance_id, fields)); return {"reservation_id": "br_" + "1" * 32}
            def commit(self, reservation_id, submit_id):
                calls.append(("commit", reservation_id, submit_id)); return {"reservation_id": reservation_id, "state": "committed", "submit_id": submit_id}
            def mark_ambiguous(self, *args, **kwargs): raise AssertionError("not ambiguous")
        class Adapter:
            def run(self, argv):
                return DreaminaResult(0, {"submit_id": "submit-1"}, None, "submit-1", "")
        with tempfile.TemporaryDirectory() as tmp:
            result = VideoService({"modes": []}, Path(tmp)).submit_with_batch_allowance(
                request, adapter=Adapter(), allowance=Allowance(), allowance_id="ba_" + "2" * 32,
                shot_id="S01", attempt=1)
        self.assertEqual(result["submit_id"], "submit-1")
        self.assertEqual(calls[0][2]["request_fingerprint"], build_video_request_fingerprint(request))
        self.assertEqual(calls[1][0], "commit")


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable(self) -> None:
        a = {"mode": "text2video", "prompt": "p", "model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 8}
        self.assertEqual(build_video_request_fingerprint(a), build_video_request_fingerprint(copy.deepcopy(a)))

    def test_fingerprint_changes_with_duration(self) -> None:
        a = {"mode": "text2video", "prompt": "p", "model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 8}
        b = dict(a, duration_seconds=10)
        self.assertNotEqual(build_video_request_fingerprint(a), build_video_request_fingerprint(b))

    def test_fingerprint_matches_shared_canonical_contract_fingerprint(self) -> None:
        from scripts.json_contracts import canonical_fingerprint
        request = {"mode": "text2video", "prompt": "p", "model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 8}
        self.assertEqual(build_video_request_fingerprint(request), canonical_fingerprint(request))

    def test_unicode_fingerprint_preserves_legacy_direct_approval_encoding(self) -> None:
        request = {"mode": "text2video", "prompt": "晨雾中的蓝色工作室", "model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 4}
        self.assertEqual(build_video_request_fingerprint(request), "f12e3b94d504052c1448f9faf982a393129e73bcfcccc85a834667d48628fb86")
        with tempfile.TemporaryDirectory() as tmp:
            guard, session_id, approval_id = issue_approval(Path(tmp), request, {})
            guard.consume_approval(session_id, request=request, approval_id=approval_id, fingerprint_builder=build_video_request_fingerprint)


class TextToVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_video_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

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
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

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

    def test_image2video_accepts_one_validated_style_reference(self) -> None:
        req = self.service.build_request(
            mode="image2video", prompt="x", model="seedance-2.5",
            video_resolution="720P", ratio="16:9",
            references=[{"path": "/style.png", "role": "style", "sha256": "a" * 64}],
        )
        self.assertEqual(req["references"], [
            {"path": "/style.png", "role": "style", "sha256": "a" * 64}
        ])

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
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

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
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

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
            reference_policy=_TestReferencePolicy(),
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
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

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
            self.service.submit(req, adapter=None, **approval_args(Path(self.tmp.name) / "approvals", req, approval), web_prerequisite_cleared=False)  # type: ignore[arg-type]

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
            self.service.submit(req, adapter=None, **approval_args(Path(self.tmp.name) / "approvals", req, approval), web_prerequisite_cleared=True)  # type: ignore[arg-type]

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
            **approval_args(Path(self.tmp.name) / "approvals", req, approval),
            web_prerequisite_cleared=False,
        )
        self.assertEqual(result["submit_id"], "vsub-1")


class ApprovalBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_video_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

    def test_submit_without_approval_raises(self) -> None:
        req = self.service.build_request(
            mode="text2video",
            prompt="x",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
        )
        with self.assertRaises(MissingApprovalError):
            self.service.submit(req, adapter=None, approval_guard=None, session_id=None, approval_id=None, web_prerequisite_cleared=False)  # type: ignore[arg-type]

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
        approved_request = dict(req)
        original_fingerprint = build_video_request_fingerprint(approved_request)
        approval = {
            "request_fingerprint": original_fingerprint,
            "acknowledged_cost": "credits",
            "acknowledged_scope": {"model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 4},
            "approved_at": "2026-09-12T00:00:00Z",
            "approver": "test",
        }
        secured = approval_args(Path(self.tmp.name) / "approvals", approved_request, approval)
        req["prompt"] = "tampered"
        self.service.record_web_prerequisite_acknowledgement()
        with self.assertRaises(ApprovalMismatchError):
            self.service.submit(req, adapter=None, **secured, web_prerequisite_cleared=False)  # type: ignore[arg-type]

    def test_submit_with_changed_duration_invalidates_approval(self) -> None:
        req = self.service.build_request(
            mode="text2video",
            prompt="x",
            model="seedance-2.5",
            video_resolution="720P",
            ratio="16:9",
            duration_seconds=8,
        )
        approved_request = dict(req)
        original_fingerprint = build_video_request_fingerprint(approved_request)
        approval = {
            "request_fingerprint": original_fingerprint,
            "acknowledged_cost": "credits",
            "acknowledged_scope": {"model": "seedance-2.5", "video_resolution": "720P", "ratio": "16:9", "duration_seconds": 8},
            "approved_at": "2026-09-12T00:00:00Z",
            "approver": "test",
        }
        secured = approval_args(Path(self.tmp.name) / "approvals", approved_request, approval)
        req["duration_seconds"] = 12
        self.service.record_web_prerequisite_acknowledgement()
        with self.assertRaises(ApprovalMismatchError):
            self.service.submit(req, adapter=None, **secured, web_prerequisite_cleared=False)  # type: ignore[arg-type]


class SubmitSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_video_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = VideoService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

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
        result = self.service.submit(req, adapter=adapter, **approval_args(Path(self.tmp.name) / "approvals", req, approval), web_prerequisite_cleared=False)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0][0], "text2video")
        self.assertIn("--video_resolution", adapter.calls[0])
        self.assertIn("720P", adapter.calls[0])
        self.assertIn("--duration", adapter.calls[0])
        self.assertIn("4", adapter.calls[0])
        self.assertEqual(result["submit_id"], "vsub-2")


class MultiFrameVideoTests(unittest.TestCase):
    def test_multiframe_transition_duration_rejects_bool_and_non_integer(self) -> None:
        snapshot = synthetic_video_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            service = VideoService(snapshot=snapshot, ledger_dir=Path(tmp), reference_policy=_TestReferencePolicy())
            for value in (True, 1.5, "2"):
                with self.subTest(value=value), self.assertRaisesRegex(Exception, "integer"):
                    service.build_request(mode="multiframe2video", prompt="story", model=None, video_resolution="720P", duration_seconds=3, references=[{"path":"/a.png","role":"frame"},{"path":"/b.png","role":"frame"}], transitions=[{"prompt":"pan","duration_seconds":value}])

    def test_multiframe_requires_live_mode_limits(self) -> None:
        snapshot = synthetic_video_snapshot()
        del snapshot["mode_limits"]
        with tempfile.TemporaryDirectory() as tmp:
            service = VideoService(snapshot=snapshot, ledger_dir=Path(tmp), reference_policy=_TestReferencePolicy())
            with self.assertRaisesRegex(UnsupportedCapabilityError, "mode limits"):
                service.build_request(mode="multiframe2video", prompt="story", model=None, video_resolution="720P", duration_seconds=3, references=[{"path":"/a.png","role":"frame"},{"path":"/b.png","role":"frame"}])

    def test_multiframe_argv_preserves_order_and_transitions(self) -> None:
        argv = VideoService._request_to_argv({"mode":"multiframe2video","prompt":"story","video_resolution":"720p","duration_seconds":3,"references":[{"path":"/a.png","role":"frame"},{"path":"/b.png","role":"frame"}],"transitions":[{"prompt":"pan","duration_seconds":3}]})
        self.assertEqual(argv, ["multiframe2video","--prompt","story","--video_resolution","720p","--duration","3","--images","/a.png,/b.png","--transition-prompt","pan","--transition-duration","3"])


if __name__ == "__main__":
    unittest.main()
