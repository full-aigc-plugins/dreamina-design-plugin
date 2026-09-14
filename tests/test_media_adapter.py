from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.media_adapter import MediaAdapter, MediaOutputError, MediaTimeoutError
from scripts.trusted_media_tools import TrustedMediaToolError, TrustedMediaToolStore


class _Approve:
    def confirm_media_tool_enrollment(self, **kwargs):
        return "approved"


class _FakeRunner:
    def __init__(self) -> None:
        self.stdout = ""
        self.stderr = ""
        self.exit_code = 0
        self.raise_timeout = False
        self.kwargs = {}
        self.argv = []
        self.calls = []
        self.process_group_terminated = False
        self.frame_bytes = b"\x89PNG\r\n\x1a\n" + b"frame"

    def run(self, argv, **kwargs):
        self.argv = list(argv)
        self.calls.append(list(argv))
        self.kwargs = kwargs
        if self.raise_timeout:
            self.process_group_terminated = bool(kwargs.get("terminate_process_group"))
            raise TimeoutError("simulated timeout")
        if "image2pipe" in argv and argv[-1] != "-":
            Path(argv[-1]).write_bytes(self.frame_bytes)
        return self.exit_code, self.stdout, self.stderr


class MediaAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.ffmpeg = self._enroll("ffmpeg")
        self.ffprobe = self._enroll("ffprobe")
        self.runner = _FakeRunner()
        self.adapter = MediaAdapter(self.store, runner=self.runner, max_output_bytes=4096)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"video")

    def _enroll(self, kind: str) -> Path:
        binary = self.root / kind
        binary.write_bytes(("trusted " + kind).encode("utf-8"))
        binary.chmod(0o755)
        if not hasattr(self, "store"):
            self.store = TrustedMediaToolStore(
                path=self.root / "config" / "trusted-media-tools.json",
                staging_root=self.root / "staged",
            )
        self.store.enroll(kind, binary, approval_provider=_Approve())
        return binary

    def test_runner_never_uses_shell_and_kills_process_group_on_timeout(self) -> None:
        self.runner.raise_timeout = True
        with self.assertRaises(MediaTimeoutError):
            self.adapter.run("ffmpeg", ["-version"], timeout_seconds=1)
        self.assertIs(self.runner.kwargs["shell"], False)
        self.assertIs(self.runner.kwargs["start_new_session"], True)
        self.assertTrue(self.runner.process_group_terminated)

    def test_run_passes_argv_minimal_environment_timeout_and_output_caps(self) -> None:
        result = self.adapter.run("ffmpeg", ["-version"], timeout_seconds=7)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.runner.argv[1:], ["-version"])
        self.assertNotEqual(self.runner.argv[0], str(self.ffmpeg))
        self.assertIs(self.runner.kwargs["shell"], False)
        self.assertEqual(self.runner.kwargs["timeout_seconds"], 7)
        self.assertEqual(self.runner.kwargs["stdout_cap"], 4096)
        self.assertEqual(self.runner.kwargs["stderr_cap"], 4096)
        self.assertEqual(
            self.runner.kwargs["env"]["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin"
        )

    def test_probe_rejects_more_than_one_json_document(self) -> None:
        self.runner.stdout = '{"streams": []}\n{"format": {}}\n'
        with self.assertRaises(MediaOutputError):
            self.adapter.probe_json(self.source)

    def test_probe_returns_exact_single_json_object(self) -> None:
        self.runner.stdout = '{"streams": [], "format": {"duration": "1.0"}}\n'
        payload = self.adapter.probe_json(self.source)
        self.assertEqual(payload["streams"], [])
        self.assertEqual(
            self.runner.argv[1:],
            ["-v", "error", "-show_streams", "-show_format", "-of", "json", str(self.source)],
        )

    def test_video_frame_verification_decodes_both_anchors(self) -> None:
        result = self.adapter.verify_video_frames(self.source, 4.0)
        digest = hashlib.sha256(self.runner.frame_bytes).hexdigest()
        self.assertEqual(result["readable"], True)
        self.assertEqual(result["start_anchor"], {"at_seconds": 0.0, "sha256": digest,
            "size_bytes": len(self.runner.frame_bytes)})
        self.assertEqual(result["end_anchor"], {"at_seconds": 3.95, "sha256": digest,
            "size_bytes": len(self.runner.frame_bytes)})
        self.assertEqual([call[call.index("-ss") + 1] for call in self.runner.calls], ["0.000", "3.950"])
        self.assertTrue(all(call[call.index("-map") + 1] == "0:v:0" for call in self.runner.calls))
        self.assertTrue(all(call[call.index("-frames:v"):call.index("-y")] ==
                            ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png"]
                            for call in self.runner.calls))

    def test_video_frame_verification_rejects_empty_success_output(self) -> None:
        self.runner.frame_bytes = b""
        with self.assertRaises(MediaOutputError):
            self.adapter.verify_video_frames(self.source, 4.0)

    def test_video_frame_verification_rejects_decoder_failure(self) -> None:
        self.runner.exit_code = 1
        with self.assertRaisesRegex(MediaOutputError, "exit 1"):
            self.adapter.verify_video_frames(self.source, 4.0)

    def test_probe_rejects_non_object_or_failed_output(self) -> None:
        for stdout, exit_code in (("[]", 0), ("not json", 0), ("{}", 2)):
            with self.subTest(stdout=stdout, exit_code=exit_code):
                self.runner.stdout = stdout
                self.runner.exit_code = exit_code
                with self.assertRaises(MediaOutputError):
                    self.adapter.probe_json(self.source)

    def test_caller_cannot_select_unenrolled_tool_kind(self) -> None:
        with self.assertRaises(TrustedMediaToolError):
            self.adapter.run("bash", ["-c", "id"], timeout_seconds=1)


if __name__ == "__main__":
    unittest.main()
