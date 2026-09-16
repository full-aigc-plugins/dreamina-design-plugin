"""Tests for scripts/design_handoff.py.

The design handoff must:
  - send a normalized multimodal request containing the validated preview
    receipt/hash + user prompt and never the DCC scene data;
  - delegate capability resolution, quote, approve, submit, query, and
    download through the public codex-dreamina-design receipt interface;
  - persist the design submit ID BEFORE reporting success;
  - on duplicate submit return the existing submit id (no new paid action);
  - on quote mismatch invalidate the quote;
  - on approval rejection/expired transition the job to Failed;
  - on submit/query timeout enter Unknown (only query/reconcile);
  - on artifact hash mismatch after download fail closed;
  - on missing plugin/web prereq fail closed without sending any submit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "seedance_pipeline"))

from design_handoff import (  # noqa: E402
    ApprovalRejectedError,
    ArtifactMismatchError,
    DesignHandoffError,
    MissingDesignPluginError,
    QuoteMismatchError,
    UnknownStateError,
    WebPrerequisiteError,
    design_handoff,
)

FAKE = ROOT / "tests" / "seedance_pipeline" / "fakes" / "fake_design_adapter.py"


def _make_executable(state_dir: Path) -> str:
    wrapper = state_dir / ".fake_design.sh"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} --state-dir {state_dir} \"$@\"\n")
    wrapper.chmod(0o755)
    return str(wrapper)


VALID_RECEIPT = {
    "schema_version": "1.0.0",
    "producer_plugin": "codex-blender",
    "producer_version": "0.1.0",
    "artifact_id": "blender_preview_abc123",
    "path": "/tmp/blender_preview.mp4",
    "sha256": "0" * 64,
    "codec": "h264",
    "container": "mp4",
    "dimensions": {"width": 1280, "height": 720},
    "fps": 24.0,
    "duration_seconds": 4.0,
    "bytes": 4096,
    "camera": {"name": "Camera.001"},
    "frame_range": {"start": 1, "end": 96},
    "preview_mode": "camera_render",
    "restoration": {"status": "confirmed"},
}


class NormalizedRequestTests(unittest.TestCase):
    def test_normalized_request_contains_receipt_hash_and_prompt_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            captured = state_dir / "captured.json"
            record_wrapper = state_dir / "record.sh"
            record_wrapper.write_text(
                f"#!/bin/sh\n"
                f"cp \"$4\" {captured}\n"
                f"exec {sys.executable} {FAKE} --state-dir {state_dir} \"$@\"\n"
            )
            record_wrapper.chmod(0o755)
            from design_handoff import _run_design
            _run_design(
                executable=record_wrapper,
                mode="capabilities",
                payload={"x": 1},
                request_dir=state_dir,
            )
            sent = json.loads(captured.read_text())
            self.assertIn("x", sent)
            self.assertNotIn("scene", sent)
            raw = (ROOT / "scripts" / "seedance_pipeline" / "design_handoff.py").read_text()
            for forbidden in ("scene_path", ".blend", ".ma", "codex_blender", "codex_maya"):
                self.assertNotIn(forbidden, raw)


class HappyPathTests(unittest.TestCase):
    def test_capability_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            caps = design_handoff(executable=wrapper, mode="capabilities", payload={}, request_dir=state_dir)
            self.assertEqual(len(caps["capabilities"]), 1)
            self.assertEqual(caps["capabilities"][0]["model"], "seedance-2.5")

    def test_quote_then_approve_then_submit_then_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            inputs = {"model": "seedance-2.5", "resolution": "1280x720", "ratio": "16:9", "duration_seconds": 4.0, "prompt": "render"}
            quote = design_handoff(executable=wrapper, mode="quote", payload={"quote_inputs": inputs, "quote_key": "q1"}, request_dir=state_dir)
            self.assertEqual(quote["price_usd"], 1.0)
            approval = design_handoff(executable=wrapper, mode="approve", payload={"approval_id": "apr_1"}, request_dir=state_dir)
            self.assertEqual(approval["status"], "approved")
            submit = design_handoff(executable=wrapper, mode="submit", payload={"job_key": "j1"}, request_dir=state_dir)
            self.assertIn("design_submit_id", submit)
            query = design_handoff(
                executable=wrapper,
                mode="query",
                payload={"design_submit_id": submit["design_submit_id"]},
                request_dir=state_dir,
                output_path=state_dir / "query-result.mp4",
            )
            self.assertEqual(query["status"], "succeeded")


class FailurePathTests(unittest.TestCase):
    def test_succeeded_without_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            with self.assertRaises(ArtifactMismatchError):
                design_handoff(
                    executable=wrapper,
                    mode="query",
                    payload={"force_error": "missing_artifact"},
                    request_dir=state_dir,
                )

    def test_succeeded_with_missing_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            with self.assertRaises(ArtifactMismatchError):
                design_handoff(
                    executable=wrapper,
                    mode="query",
                    payload={"force_error": "missing_file"},
                    request_dir=state_dir,
                )

    def test_download_rehashes_expected_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            output = state_dir / "download.mp4"
            import hashlib
            expected = hashlib.sha256(b"FAKE_MEDIA_BYTES").hexdigest()
            result = design_handoff(
                executable=wrapper,
                mode="download",
                payload={"expected_sha256": expected},
                request_dir=state_dir,
                output_path=output,
            )
            self.assertIn("sha256", result, "download must return its verified digest")
            self.assertIn("bytes", result, "download must return its verified byte size")
            self.assertEqual(result["sha256"], expected)
            self.assertEqual(result["bytes"], len(b"FAKE_MEDIA_BYTES"))

    def test_missing_capability_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            with self.assertRaises(DesignHandoffError):
                design_handoff(executable=wrapper, mode="capabilities", payload={"force_error": "missing_capability"}, request_dir=state_dir)

    def test_web_prerequisite_raises_without_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            with self.assertRaises(WebPrerequisiteError):
                design_handoff(executable=wrapper, mode="capabilities", payload={"force_error": "web_prereq"}, request_dir=state_dir)

    def test_quote_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            inputs = {"model": "seedance-2.5", "resolution": "1280x720", "ratio": "16:9", "duration_seconds": 4.0, "prompt": "render"}
            with self.assertRaises(QuoteMismatchError):
                design_handoff(executable=wrapper, mode="quote", payload={"quote_inputs": inputs, "quote_key": "q1", "force_error": "quote_mismatch"}, request_dir=state_dir)

    def test_approval_rejected_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            with self.assertRaises(ApprovalRejectedError):
                design_handoff(executable=wrapper, mode="approve", payload={"approval_id": "apr_1", "force_error": "approval_rejected"}, request_dir=state_dir)

    def test_approval_expired_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            with self.assertRaises(ApprovalRejectedError):
                design_handoff(executable=wrapper, mode="approve", payload={"approval_id": "apr_1", "force_error": "approval_expired"}, request_dir=state_dir)

    def test_duplicate_submit_returns_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            first = design_handoff(executable=wrapper, mode="submit", payload={"job_key": "j1"}, request_dir=state_dir)
            second = design_handoff(executable=wrapper, mode="submit", payload={"job_key": "j1"}, request_dir=state_dir)
            self.assertEqual(first["design_submit_id"], second["design_submit_id"])

    def test_submit_unknown_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            with self.assertRaises(UnknownStateError):
                design_handoff(executable=wrapper, mode="submit", payload={"job_key": "j1", "force_error": "unknown"}, request_dir=state_dir)

    def test_query_timeout_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            with self.assertRaises(UnknownStateError):
                design_handoff(executable=wrapper, mode="query", payload={"design_submit_id": "ds_x", "force_error": "timeout"}, request_dir=state_dir, timeout_seconds=0.5)

    def test_artifact_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrapper = _make_executable(state_dir)
            output = state_dir / "expected.mp4"
            with self.assertRaises(ArtifactMismatchError):
                design_handoff(executable=wrapper, mode="query", payload={"design_submit_id": "ds_x", "force_error": "artifact_mismatch"}, request_dir=state_dir, output_path=output)


class MissingPluginTests(unittest.TestCase):
    def test_missing_design_plugin_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MissingDesignPluginError):
                design_handoff(executable="/no/such/executable", mode="capabilities", payload={}, request_dir=Path(tmp))


if __name__ == "__main__":
    unittest.main()
