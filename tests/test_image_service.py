"""RED tests for the Dreamina image workflow service (Task 3).

Covers the image request contract:

* text2image / image2image mode selection from a synthetic capability
  snapshot;
* batch count discovery and validation (1–10 inclusive);
* required ``resolution_type`` per model token;
* paired width / height vs ratio exclusivity (already in schema, asserted
  again at the service layer for round-trip integrity);
* unsupported model / resolution / ratio tokens are rejected before the
  adapter is touched;
* reference scope is validated per mode (image2image requires at least
  one ``subject`` role; text2image rejects references);
* request fingerprint is stable and approval-bound (approval_receipt
  rejected when fingerprint drifts).
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.dreamina_adapter import DreaminaResult  # noqa: E402
from scripts.approval_guard import ApprovalGuard  # noqa: E402


class _TestReferencePolicy:
    def validate(self, reference):
        return dict(reference)

# Importing the module under test is expected to fail until Task 3.3 lands.
try:  # pragma: no cover - exercised by RED phase
    from scripts.image_service import (  # type: ignore  # noqa: E402
        ApprovalMismatchError,
        BatchCountOutOfRangeError,
        ImageService,
        InvalidReferenceError,
        MissingApprovalError,
        UnsupportedCapabilityError,
        build_request_fingerprint,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by RED phase
    ImageService = None  # type: ignore[assignment]
    ApprovalMismatchError = None  # type: ignore[assignment]
    BatchCountOutOfRangeError = None  # type: ignore[assignment]
    InvalidReferenceError = None  # type: ignore[assignment]
    MissingApprovalError = None  # type: ignore[assignment]
    UnsupportedCapabilityError = None  # type: ignore[assignment]
    build_request_fingerprint = None  # type: ignore[assignment]


def synthetic_image_snapshot() -> dict:
    """A small, deterministic capability snapshot covering both image modes."""
    return {
        "cli_version": "1.4.18",
        "captured_at": "2026-09-12T00:00:00Z",
        "modes": ["text2image", "image2image"],
        "models": [
            {
                "name": "seedream-5.0-pro",
                "modes": ["text2image", "image2image"],
                "resolutions": ["1k", "1.5k", "2k"],
                "ratios": ["1:1", "3:4", "4:3", "16:9", "9:16"],
                "max_count": 10,
            },
            {
                "name": "seedream-4.0",
                "modes": ["text2image", "image2image"],
                "resolutions": ["1k", "2k"],
                "ratios": ["1:1", "16:9"],
                "max_count": 4,
            },
        ],
        "resolutions": {"image": ["1k", "1.5k", "2k"]},
        "ratios": ["1:1", "3:4", "4:3", "16:9", "9:16"],
    }


def issue_approval(root: Path, request: dict, scope: dict):
    scope = {
        "count": request.get("count", 1),
        "model": request.get("model"),
        "resolution": request.get("resolution_type"),
        **scope,
    }
    guard = ApprovalGuard(root=root)
    session_id = guard.create_session(label="submission")
    approval_id = guard.record_approval(
        session_id,
        request=request,
        receipt={
            "request_fingerprint": build_request_fingerprint(request),
            "acknowledged_cost": "credits",
            "acknowledged_scope": scope,
            "approved_at": "2026-09-12T00:00:00Z",
            "approver": "test",
        },
    )
    return guard, session_id, approval_id


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_is_deterministic(self) -> None:
        payload_a = {"mode": "text2image", "prompt": "a mountain", "model": "seedream-5.0-pro", "count": 1, "resolution_type": "1k"}
        payload_b = copy.deepcopy(payload_a)
        self.assertEqual(build_request_fingerprint(payload_a), build_request_fingerprint(payload_b))

    def test_fingerprint_changes_with_input(self) -> None:
        payload_a = {"mode": "text2image", "prompt": "a mountain", "model": "seedream-5.0-pro", "count": 1, "resolution_type": "1k"}
        payload_b = dict(payload_a, count=2)
        self.assertNotEqual(build_request_fingerprint(payload_a), build_request_fingerprint(payload_b))


class ImageServiceConstructionTests(unittest.TestCase):
    def test_module_exports_service(self) -> None:
        self.assertIsNotNone(ImageService)


class TextToImageRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_image_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = ImageService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

    def test_text2image_uses_default_count_one(self) -> None:
        request = self.service.build_request(
            mode="text2image",
            prompt="a calm mountain",
            model="seedream-5.0-pro",
            resolution_type="1k",
        )
        self.assertEqual(request["count"], 1)
        self.assertEqual(request["mode"], "text2image")
        self.assertEqual(request["resolution_type"], "1k")

    def test_text2image_with_explicit_count(self) -> None:
        request = self.service.build_request(
            mode="text2image",
            prompt="a calm mountain",
            model="seedream-5.0-pro",
            resolution_type="1k",
            count=4,
        )
        self.assertEqual(request["count"], 4)

    def test_text2image_requires_resolution_type(self) -> None:
        with self.assertRaises(UnsupportedCapabilityError):
            self.service.build_request(
                mode="text2image",
                prompt="a calm mountain",
                model="seedream-5.0-pro",
            )

    def test_text2image_rejects_unknown_resolution(self) -> None:
        with self.assertRaises(UnsupportedCapabilityError):
            self.service.build_request(
                mode="text2image",
                prompt="a calm mountain",
                model="seedream-5.0-pro",
                resolution_type="4k",
            )

    def test_text2image_rejects_unknown_ratio(self) -> None:
        with self.assertRaises(UnsupportedCapabilityError):
            self.service.build_request(
                mode="text2image",
                prompt="a calm mountain",
                model="seedream-5.0-pro",
                resolution_type="1k",
                ratio="21:9",
            )

    def test_text2image_rejects_references(self) -> None:
        with self.assertRaises(InvalidReferenceError):
            self.service.build_request(
                mode="text2image",
                prompt="a calm mountain",
                model="seedream-5.0-pro",
                resolution_type="1k",
                references=[{"path": "/tmp/x.png", "role": "subject"}],
            )

    def test_text2image_width_height_must_be_paired(self) -> None:
        with self.assertRaises(UnsupportedCapabilityError):
            self.service.build_request(
                mode="text2image",
                prompt="a calm mountain",
                model="seedream-5.0-pro",
                resolution_type="1k",
                width=1024,
            )

    def test_text2image_width_height_mutually_exclusive_with_ratio(self) -> None:
        with self.assertRaises(UnsupportedCapabilityError):
            self.service.build_request(
                mode="text2image",
                prompt="a calm mountain",
                model="seedream-5.0-pro",
                resolution_type="1k",
                width=1024,
                height=1024,
                ratio="1:1",
            )


class ImageToImageRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_image_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = ImageService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

    def test_image2image_requires_subject_reference(self) -> None:
        with self.assertRaises(InvalidReferenceError):
            self.service.build_request(
                mode="image2image",
                prompt="a stylized mountain",
                model="seedream-5.0-pro",
                resolution_type="1k",
                references=[{"path": "/tmp/ref.png", "role": "style"}],
            )

    def test_image2image_rejects_upload_without_reference_policy(self) -> None:
        service = ImageService(
            snapshot=self.snapshot,
            ledger_dir=Path(self.tmp.name) / "untrusted-ledger",
        )
        with self.assertRaises(InvalidReferenceError):
            service.build_request(
                mode="image2image",
                prompt="x",
                model="seedream-5.0-pro",
                resolution_type="1k",
                references=[{"path": "/tmp/input.png", "role": "subject"}],
            )

    def test_image2image_accepts_subject_reference(self) -> None:
        request = self.service.build_request(
            mode="image2image",
            prompt="a stylized mountain",
            model="seedream-5.0-pro",
            resolution_type="1k",
            references=[{"path": "/tmp/ref.png", "role": "subject", "mime_type": "image/png", "size_bytes": 1024}],
        )
        self.assertEqual(request["mode"], "image2image")
        self.assertEqual(len(request["references"]), 1)
        self.assertEqual(request["references"][0]["role"], "subject")

    def test_image2image_rejects_too_many_references(self) -> None:
        refs = [
            {"path": f"/tmp/r{i}.png", "role": "style", "mime_type": "image/png", "size_bytes": 100}
            for i in range(9)
        ]
        with self.assertRaises(InvalidReferenceError):
            self.service.build_request(
                mode="image2image",
                prompt="a stylized mountain",
                model="seedream-5.0-pro",
                resolution_type="1k",
                references=refs,
            )


class BatchCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_image_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = ImageService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

    def test_count_zero_is_rejected(self) -> None:
        with self.assertRaises(BatchCountOutOfRangeError):
            self.service.build_request(
                mode="text2image",
                prompt="x",
                model="seedream-5.0-pro",
                resolution_type="1k",
                count=0,
            )

    def test_count_above_ten_is_rejected(self) -> None:
        with self.assertRaises(BatchCountOutOfRangeError):
            self.service.build_request(
                mode="text2image",
                prompt="x",
                model="seedream-5.0-pro",
                resolution_type="1k",
                count=11,
            )

    def test_count_above_model_max_is_rejected(self) -> None:
        """seedream-4.0 advertises max_count=4."""
        with self.assertRaises(BatchCountOutOfRangeError):
            self.service.build_request(
                mode="text2image",
                prompt="x",
                model="seedream-4.0",
                resolution_type="1k",
                count=5,
            )


class UnsupportedModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_image_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = ImageService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

    def test_unknown_model_is_rejected(self) -> None:
        with self.assertRaises(UnsupportedCapabilityError):
            self.service.build_request(
                mode="text2image",
                prompt="x",
                model="dreamina-flux-9000",
                resolution_type="1k",
            )

    def test_model_not_in_mode_is_rejected(self) -> None:
        """A model that exists but lacks the requested mode."""
        snapshot = dict(self.snapshot)
        snapshot["models"] = [
            {"name": "image-only-model", "modes": ["image2image"], "resolutions": ["1k"], "ratios": ["1:1"], "max_count": 1}
        ]
        service = ImageService(snapshot=snapshot, ledger_dir=Path(self.tmp.name) / "ledger2", reference_policy=_TestReferencePolicy())
        with self.assertRaises(UnsupportedCapabilityError):
            service.build_request(
                mode="text2image",
                prompt="x",
                model="image-only-model",
                resolution_type="1k",
            )


class ApprovalBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_image_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = ImageService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

    def test_submit_without_approval_raises(self) -> None:
        request = self.service.build_request(
            mode="text2image",
            prompt="x",
            model="seedream-5.0-pro",
            resolution_type="1k",
        )
        with self.assertRaises(MissingApprovalError):
            self.service.submit(request, adapter=None, approval_guard=None, session_id=None, approval_id=None)  # type: ignore[arg-type]

    def test_submit_with_mismatched_approval_raises(self) -> None:
        request = self.service.build_request(
            mode="text2image",
            prompt="x",
            model="seedream-5.0-pro",
            resolution_type="1k",
        )
        guard, session_id, _ = issue_approval(Path(self.tmp.name) / "approvals", request, {"count": 1})
        with self.assertRaises(ApprovalMismatchError):
            self.service.submit(request, adapter=None, approval_guard=guard, session_id=session_id, approval_id="wrong")  # type: ignore[arg-type]


class SubmitSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = synthetic_image_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = ImageService(snapshot=self.snapshot, ledger_dir=Path(self.tmp.name) / "ledger", reference_policy=_TestReferencePolicy())

    def test_submit_invokes_adapter_once_for_batch(self) -> None:
        """A batch of N must be submitted as a single adapter call."""
        request = self.service.build_request(
            mode="text2image",
            prompt="x",
            model="seedream-5.0-pro",
            resolution_type="1k",
            count=3,
        )
        guard, session_id, approval_id = issue_approval(Path(self.tmp.name) / "approvals", request, {"count": 3, "model": "seedream-5.0-pro", "resolution": "1k"})

        class _StubAdapter:
            def __init__(self) -> None:
                self.calls: list[list[str]] = []

            def run(self, args):
                self.calls.append(list(args))
                return DreaminaResult(
                    exit_code=0,
                    payload={"submit_id": "sub-1", "items": [
                        {"submit_id": "sub-1", "index": 0},
                        {"submit_id": "sub-1", "index": 1},
                        {"submit_id": "sub-1", "index": 2},
                    ]},
                    error_code=None,
                    submit_id="sub-1",
                    stderr="",
                )

        adapter = _StubAdapter()
        result = self.service.submit(request, adapter=adapter, approval_guard=guard, session_id=session_id, approval_id=approval_id)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0][0], "text2image")
        self.assertIn("--generate_num", adapter.calls[0])
        self.assertIn("3", adapter.calls[0])
        self.assertEqual(result["submit_id"], "sub-1")
        self.assertEqual(len(result["items"]), 3)

    def test_submit_preserves_per_item_results(self) -> None:
        request = self.service.build_request(
            mode="text2image",
            prompt="x",
            model="seedream-5.0-pro",
            resolution_type="1k",
            count=2,
        )
        guard, session_id, approval_id = issue_approval(Path(self.tmp.name) / "approvals", request, {"count": 2, "model": "seedream-5.0-pro", "resolution": "1k"})

        class _StubAdapter:
            def run(self, args):
                return DreaminaResult(
                    exit_code=0,
                    payload={"submit_id": "sub-2", "items": [
                        {"submit_id": "sub-2", "index": 0, "url": "https://x/0"},
                        {"submit_id": "sub-2", "index": 1, "url": "https://x/1"},
                    ]},
                    error_code=None,
                    submit_id="sub-2",
                    stderr="",
                )

        adapter = _StubAdapter()
        result = self.service.submit(request, adapter=adapter, approval_guard=guard, session_id=session_id, approval_id=approval_id)
        self.assertEqual([item["index"] for item in result["items"]], [0, 1])
        self.assertEqual([item["url"] for item in result["items"]], ["https://x/0", "https://x/1"])


class ImageUpscaleTests(unittest.TestCase):
    def test_upscale_argv_contains_only_reference_resolution_and_poll(self) -> None:
        argv = ImageService._request_to_argv({"mode": "image_upscale", "resolution_type": "4k", "references": [{"path": "/safe/input.png", "role": "subject"}]})
        self.assertEqual(argv, ["image_upscale", "--image", "/safe/input.png", "--resolution_type", "4k", "--poll", "0"])


if __name__ == "__main__":
    unittest.main()
