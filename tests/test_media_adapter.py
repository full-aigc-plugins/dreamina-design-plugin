from __future__ import annotations

import hashlib
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.media_adapter import (
    MediaAdapter,
    MediaOutputError,
    MediaTimeoutError,
    SubprocessMediaRunner,
)
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
        self.binary_calls = []
        self.raise_output = False

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

    def run_binary(self, argv, **kwargs):
        self.argv = list(argv)
        self.calls.append(list(argv))
        self.binary_calls.append(list(argv))
        self.kwargs = kwargs
        if self.raise_output:
            raise MediaOutputError("simulated bounded-output failure")
        return self.exit_code, self.frame_bytes, self.stderr


class _SetupPipe:
    def __init__(self, descriptor: int, *, close_error: bool = False) -> None:
        self.descriptor = descriptor
        self.close_error = close_error
        self.closed = False

    def fileno(self) -> int:
        return self.descriptor

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise RuntimeError("pipe-close-cleanup")


class _SetupProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.stdout = _SetupPipe(10)
        self.stderr = _SetupPipe(11)
        self.running = True
        self.wait_calls = []

    def poll(self):
        return None if self.running else 0

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.running = False
        return 0


class _RegisterFailSelector:
    def __init__(self, fail_on: int, *, close_error: bool = False) -> None:
        self.fail_on = fail_on
        self.close_error = close_error
        self.register_calls = 0
        self.closed = False

    def register(self, *args) -> None:
        self.register_calls += 1
        if self.register_calls == self.fail_on:
            raise LookupError(f"register-{self.fail_on}")

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise RuntimeError("selector-close-cleanup")


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
        self.assertTrue(all(call[call.index("-frames:v"):] ==
                            ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
                            for call in self.runner.calls))
        self.assertEqual(len(self.runner.binary_calls), 2)

    def test_video_frame_verification_requires_png_not_jpeg(self) -> None:
        self.runner.frame_bytes = b"\xff\xd8\xffjpeg"
        with self.assertRaisesRegex(MediaOutputError, "valid PNG"):
            self.adapter.verify_video_frames(self.source, 4.0)

    def test_binary_runner_terminates_oversized_stream_before_completion(self) -> None:
        marker = self.root / "completed"
        code = (
            "import os,sys,time\n"
            "chunk=b'x'*1024\n"
            "for _ in range(1024):\n"
            " os.write(sys.stdout.fileno(),chunk); time.sleep(0.002)\n"
            f"open({str(marker)!r},'wb').write(b'done')\n"
        )
        with self.assertRaisesRegex(MediaOutputError, "stdout exceeded 4096 bytes"):
            SubprocessMediaRunner().run_binary(
                [sys.executable, "-c", code],
                shell=False,
                env={"PATH": os.environ.get("PATH", "")},
                timeout_seconds=5,
                stdout_cap=4096,
                stderr_cap=128,
                start_new_session=True,
                terminate_process_group=True,
            )
        self.assertFalse(marker.exists())

    def test_binary_runner_caps_stderr_without_decoding_stdout(self) -> None:
        code = "import os,sys; os.write(sys.stdout.fileno(),b'\\x89PNG\\r\\n\\x1a\\n'); os.write(sys.stderr.fileno(),b'e'*129)"
        with self.assertRaisesRegex(MediaOutputError, "stderr exceeded 128 bytes"):
            SubprocessMediaRunner().run_binary(
                [sys.executable, "-c", code],
                shell=False,
                env={"PATH": os.environ.get("PATH", "")},
                timeout_seconds=5,
                stdout_cap=4096,
                stderr_cap=128,
                start_new_session=True,
                terminate_process_group=True,
            )

    def test_binary_runner_accepts_exact_stdout_and_stderr_caps(self) -> None:
        code = ("import os,sys; os.write(sys.stdout.fileno(),b'x'*4096); "
                "os.write(sys.stderr.fileno(),b'e'*128)")
        exit_code, stdout, stderr = SubprocessMediaRunner().run_binary(
            [sys.executable, "-c", code],
            shell=False,
            env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=5,
            stdout_cap=4096,
            stderr_cap=128,
            start_new_session=True,
            terminate_process_group=True,
        )
        self.assertEqual((exit_code, len(stdout), stderr), (0, 4096, "e" * 128))
        self.assertIsInstance(stdout, bytes)

    def test_binary_runner_rejects_stdout_cap_plus_one(self) -> None:
        code = "import os,sys; os.write(sys.stdout.fileno(),b'x'*4097)"
        with self.assertRaisesRegex(MediaOutputError, "stdout exceeded 4096 bytes"):
            SubprocessMediaRunner().run_binary(
                [sys.executable, "-c", code],
                shell=False,
                env={"PATH": os.environ.get("PATH", "")},
                timeout_seconds=5,
                stdout_cap=4096,
                stderr_cap=128,
                start_new_session=True,
                terminate_process_group=True,
            )

    def test_binary_runner_reaps_and_closes_pipes_when_selector_construction_fails(self) -> None:
        process = _SetupProcess()
        with mock.patch("scripts.media_adapter.subprocess.Popen", return_value=process), \
                mock.patch("scripts.media_adapter.selectors.DefaultSelector", side_effect=LookupError("selector-create")), \
                mock.patch("scripts.media_adapter.os.killpg") as killpg:
            with self.assertRaisesRegex(LookupError, "selector-create"):
                self._run_setup_failure()
        killpg.assert_called_once_with(process.pid, signal.SIGTERM)
        self.assertFalse(process.running)
        self.assertEqual(process.wait_calls, [1.0])
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_binary_runner_cleans_selector_and_child_when_either_registration_fails(self) -> None:
        for fail_on in (1, 2):
            with self.subTest(fail_on=fail_on):
                process = _SetupProcess()
                selector = _RegisterFailSelector(fail_on)
                with mock.patch("scripts.media_adapter.subprocess.Popen", return_value=process), \
                        mock.patch("scripts.media_adapter.selectors.DefaultSelector", return_value=selector), \
                        mock.patch("scripts.media_adapter.os.killpg"):
                    with self.assertRaisesRegex(LookupError, f"register-{fail_on}"):
                        self._run_setup_failure()
                self.assertFalse(process.running)
                self.assertTrue(process.stdout.closed)
                self.assertTrue(process.stderr.closed)
                self.assertTrue(selector.closed)

    def test_binary_runner_preserves_setup_error_when_cleanup_also_fails(self) -> None:
        process = _SetupProcess()
        process.stdout.close_error = True
        process.stderr.close_error = True
        selector = _RegisterFailSelector(2, close_error=True)
        with mock.patch("scripts.media_adapter.subprocess.Popen", return_value=process), \
                mock.patch("scripts.media_adapter.selectors.DefaultSelector", return_value=selector), \
                mock.patch("scripts.media_adapter.os.killpg"):
            with self.assertRaisesRegex(LookupError, "register-2"):
                self._run_setup_failure()
        self.assertFalse(process.running)
        self.assertEqual(process.wait_calls, [1.0])
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertTrue(selector.closed)

    def test_binary_runner_reaps_child_when_process_group_is_already_gone(self) -> None:
        process = _SetupProcess()
        with mock.patch("scripts.media_adapter.subprocess.Popen", return_value=process), \
                mock.patch("scripts.media_adapter.selectors.DefaultSelector", side_effect=LookupError("selector-create")), \
                mock.patch("scripts.media_adapter.os.killpg", side_effect=ProcessLookupError):
            with self.assertRaisesRegex(LookupError, "selector-create"):
                self._run_setup_failure()
        self.assertFalse(process.running)
        self.assertEqual(process.wait_calls, [1.0])
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    @staticmethod
    def _run_setup_failure() -> None:
        SubprocessMediaRunner().run_binary(
            ["trusted-tool"], shell=False, env={}, timeout_seconds=1,
            stdout_cap=4, stderr_cap=4, start_new_session=True,
            terminate_process_group=True,
        )

    def test_binary_frame_failure_releases_trusted_staging(self) -> None:
        self.runner.raise_output = True
        with self.assertRaisesRegex(MediaOutputError, "bounded-output failure"):
            self.adapter.verify_video_frames(self.source, 4.0)
        self.assertEqual(list((self.root / "staged").iterdir()), [])

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
