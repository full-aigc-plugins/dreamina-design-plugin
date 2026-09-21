"""Tests for scripts/dcc_handoff.py.

The DCC handoff must:
  - inspect the scene before exporting;
  - invoke companion adapters via argv and JSON contracts only (never by
    importing their internal Python modules);
  - preserve the producer's receipt and re-hash the artifact independently;
  - on adapter error / timeout / unsupported media / stale output, return a
    classified failure rather than auto-retry;
  - on timeout, query the adapter's status if supported and otherwise return
    Unknown without re-running the export.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "seedance_pipeline"))

from dcc_handoff import (
    AdapterError,
    CompanionDccPreviewPort,
    InspectionResult,
    PreviewSpec,
    StaleOutputError,
    UnsupportedMediaError,
    inspect_scene,
    request_preview_export,
)

FAKES = ROOT / "tests" / "seedance_pipeline" / "fakes"
BLENDER = str(FAKES / "fake_blender_adapter.py")
MAYA = str(FAKES / "fake_maya_adapter.py")


def _python_executable() -> str:
    return sys.executable


def _make_blender_executable() -> str:
    """Return the executable fake without mutating tracked fixtures."""
    return BLENDER


def _make_maya_executable() -> str:
    return MAYA


class InspectSceneTests(unittest.TestCase):
    def test_inspect_returns_scene_summary(self) -> None:
        exe = _make_blender_executable()
        result = inspect_scene(executable=exe, scene="/tmp/scene.blend")
        self.assertIsInstance(result, InspectionResult)
        self.assertEqual(result.status, "inspect_ok")

    def test_inspect_passes_request_as_argv(self) -> None:
        # Use a wrapper that records argv via a python helper and exits 0.
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "argv.json"
            helper = Path(tmp) / "record_argv.py"
            helper.write_text(
                "import json, sys\n"
                f"open({str(record)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:]}}))\n"
            )
            wrapper = Path(tmp) / "blender.sh"
            wrapper.write_text(
                f"#!/bin/sh\nexec {_python_executable()} {helper} \"$@\"\n"
            )
            wrapper.chmod(0o755)
            inspect_scene(executable=str(wrapper), scene="/tmp/scene.blend")
            self.assertTrue(record.is_file())
            argv = json.loads(record.read_text())["argv"]
            joined = " ".join(argv)
            self.assertIn("--request", joined)
            self.assertIn("--receipt", joined)


class RequestPreviewExportTests(unittest.TestCase):
    def test_companion_port_returns_visual_loop_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = CompanionDccPreviewPort(
                executable=_make_blender_executable(), output_dir=tmp
            ).capture({
                "scene": "/tmp/scene.blend", "camera_name": "Camera.001",
                "frame_start": 1, "frame_end": 96, "output_label": "preview",
                "artifact_id": "visual_loop_preview",
            })
        self.assertEqual(artifact["mime_type"], "video/mp4")
        self.assertEqual(len(artifact["sha256"]), 64)
        self.assertGreater(artifact["size_bytes"], 0)

    def test_blender_export_returns_validated_receipt(self) -> None:
        exe = _make_blender_executable()
        spec = PreviewSpec(scene="/tmp/scene.blend", camera_name="Camera.001", frame_start=1, frame_end=96, output_label="preview")
        with tempfile.TemporaryDirectory() as tmp:
            receipt, revalidated = request_preview_export(executable=exe, spec=spec, output_dir=tmp, artifact_id="blender_test")
            self.assertEqual(receipt["producer_plugin"], "blender-design")
            self.assertEqual(receipt["restoration"]["status"], "confirmed")
            # The handoff must have independently re-hashed the file.
            self.assertEqual(receipt["sha256"], revalidated)

    def test_maya_export_returns_validated_receipt(self) -> None:
        exe = _make_maya_executable()
        spec = PreviewSpec(scene="/tmp/scene.ma", camera_name="perspShape", frame_start=1, frame_end=90, output_label="playblast")
        with tempfile.TemporaryDirectory() as tmp:
            receipt, revalidated = request_preview_export(executable=exe, spec=spec, output_dir=tmp, artifact_id="maya_test")
            self.assertEqual(receipt["producer_plugin"], "maya-design")
            self.assertEqual(receipt["sha256"], revalidated)

    def test_adapter_error_is_classified(self) -> None:
        exe = _make_blender_executable()
        spec = PreviewSpec(scene="/tmp/scene.blend", camera_name="Camera.001", frame_start=1, frame_end=96, output_label="preview")
        with tempfile.TemporaryDirectory() as tmp:
            request_path = Path(tmp) / "request.json"
            request_path.write_text(json.dumps({"force_error": "adapter", "artifact_id": "x", "output_bytes": 16}))
            with self.assertRaises(AdapterError):
                request_preview_export(executable=exe, spec=spec, output_dir=tmp, artifact_id="x", request_payload={"force_error": "adapter"})

    def test_unsupported_media_is_rejected(self) -> None:
        exe = _make_blender_executable()
        spec = PreviewSpec(scene="/tmp/scene.blend", camera_name="Camera.001", frame_start=1, frame_end=96, output_label="preview")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UnsupportedMediaError):
                request_preview_export(executable=exe, spec=spec, output_dir=tmp, artifact_id="x", request_payload={"force_error": "unsupported"})

    def test_stale_output_is_rejected(self) -> None:
        exe = _make_blender_executable()
        spec = PreviewSpec(scene="/tmp/scene.blend", camera_name="Camera.001", frame_start=1, frame_end=96, output_label="preview")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(StaleOutputError):
                request_preview_export(executable=exe, spec=spec, output_dir=tmp, artifact_id="x", request_payload={"force_error": "stale", "output_bytes": 32})

    def test_timeout_falls_back_to_status_query(self) -> None:
        # The fake adapter would sleep 5s on force_error='timeout' but we
        # override that by setting the timeout to a very small value AND
        # using a wrapper that responds to --status with 'unknown'.
        exe = _make_blender_executable()
        spec = PreviewSpec(scene="/tmp/scene.blend", camera_name="Camera.001", frame_start=1, frame_end=96, output_label="preview")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AdapterError) as ctx:
                request_preview_export(executable=exe, spec=spec, output_dir=tmp, artifact_id="x", request_payload={"force_error": "timeout", "output_bytes": 16}, timeout_seconds=0.5)
            self.assertEqual(ctx.exception.status, "unknown")

    def test_restoration_unknown_is_rejected(self) -> None:
        exe = _make_blender_executable()
        spec = PreviewSpec(scene="/tmp/scene.blend", camera_name="Camera.001", frame_start=1, frame_end=96, output_label="preview")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AdapterError) as ctx:
                request_preview_export(executable=exe, spec=spec, output_dir=tmp, artifact_id="x", request_payload={"restore_status": "unknown", "output_bytes": 16})
            self.assertIn("restoration", str(ctx.exception).lower())


class NoInternalImportsTests(unittest.TestCase):
    def test_dcc_handoff_does_not_import_companion_modules(self) -> None:
        source = (ROOT / "scripts" / "seedance_pipeline" / "dcc_handoff.py").read_text()
        for forbidden in ("blender_design", "maya_design", "from blender", "import maya"):
            self.assertNotIn(forbidden, source, f"dcc_handoff.py references companion internals: {forbidden!r}")


if __name__ == "__main__":
    unittest.main()
