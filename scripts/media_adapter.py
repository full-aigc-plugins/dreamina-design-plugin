"""Argv-only, bounded execution for enrolled local media tools."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from scripts.trusted_media_tools import TrustedMediaToolStore


DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024


class MediaAdapterError(RuntimeError):
    """Base error for local media execution."""


class MediaTimeoutError(MediaAdapterError):
    """A media process exceeded its one permitted process lifetime."""


class MediaOutputError(MediaAdapterError):
    """A media process emitted invalid, failed, or oversized output."""


@dataclass(frozen=True)
class MediaResult:
    """Bounded stdout and stderr from one media process."""

    exit_code: int
    stdout: str
    stderr: str


class SubprocessMediaRunner:
    """Stream a subprocess with independent byte caps and process-group cleanup."""

    def run(
        self,
        argv: Sequence[str],
        *,
        shell: bool,
        env: Mapping[str, str],
        timeout_seconds: int,
        stdout_cap: int,
        stderr_cap: int,
        start_new_session: bool,
        terminate_process_group: bool,
    ) -> tuple[int, str, str]:
        process = subprocess.Popen(  # noqa: S603
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=shell,
            env=dict(env),
            start_new_session=start_new_session,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, ("stdout", stdout_cap))
        selector.register(process.stderr, selectors.EVENT_READ, ("stderr", stderr_cap))
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + timeout_seconds
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate(process, terminate_process_group)
                    raise TimeoutError("media process timed out")
                for key, _ in selector.select(timeout=min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    stream, cap = key.data
                    buffers[stream].extend(chunk)
                    if len(buffers[stream]) > cap:
                        self._terminate(process, terminate_process_group)
                        raise MediaOutputError(f"media process {stream} exceeded {cap} bytes")
            exit_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        finally:
            selector.close()
            if process.poll() is None:
                self._terminate(process, terminate_process_group)
            process.stdout.close()
            process.stderr.close()
        return (
            exit_code,
            buffers["stdout"].decode("utf-8", errors="replace"),
            buffers["stderr"].decode("utf-8", errors="replace"),
        )

    @staticmethod
    def _terminate(process: subprocess.Popen, process_group: bool) -> None:
        try:
            if process_group:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.wait()


class MediaAdapter:
    """Resolve only enrolled tools and execute a fresh verified copy per call."""

    def __init__(
        self,
        tool_store: TrustedMediaToolStore,
        *,
        runner=None,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self._tool_store = tool_store
        self._runner = runner or SubprocessMediaRunner()
        self.max_output_bytes = max_output_bytes
        self.env = self._minimal_environment()

    def run(self, kind: str, argv: Sequence[str], *, timeout_seconds: int) -> MediaResult:
        """Execute one enrolled kind with an argv-only, bounded process."""
        if isinstance(argv, (str, bytes)) or not all(isinstance(arg, str) for arg in argv):
            raise TypeError("argv must be a sequence of strings")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        tools = self._tool_store.load_required({kind})
        tool = tools[kind]
        try:
            try:
                exit_code, stdout, stderr = self._runner.run(
                    [tool.staged_path, *argv],
                    shell=False,
                    env=self.env,
                    timeout_seconds=timeout_seconds,
                    stdout_cap=self.max_output_bytes,
                    stderr_cap=self.max_output_bytes,
                    start_new_session=True,
                    terminate_process_group=True,
                )
            except (TimeoutError, subprocess.TimeoutExpired) as exc:
                raise MediaTimeoutError(
                    f"{kind} timed out after {timeout_seconds}s; operation NOT resubmitted"
                ) from exc
            return MediaResult(exit_code=exit_code, stdout=stdout, stderr=stderr)
        finally:
            self._tool_store.release(tool)

    def probe_json(self, path: Path) -> dict[str, object]:
        """Run ffprobe and require exactly one JSON object on stdout."""
        source = Path(path)
        if not source.is_absolute():
            raise MediaOutputError("ffprobe source path must be absolute")
        result = self.run(
            "ffprobe",
            ["-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
            timeout_seconds=30,
        )
        if result.exit_code != 0:
            raise MediaOutputError(
                f"ffprobe failed with exit {result.exit_code}: {result.stderr.strip()}"
            )
        decoder = json.JSONDecoder()
        text = result.stdout.lstrip()
        try:
            payload, end = decoder.raw_decode(text)
        except json.JSONDecodeError as exc:
            raise MediaOutputError("ffprobe did not emit valid JSON") from exc
        if text[end:].strip():
            raise MediaOutputError("ffprobe emitted more than one JSON document")
        if not isinstance(payload, dict):
            raise MediaOutputError("ffprobe JSON output must be an object")
        return payload

    def verify_video_frames(self, path: Path, duration_seconds: float) -> dict[str, bool]:
        """Decode one frame at both clip anchors through the enrolled ffmpeg."""
        if not Path(path).is_absolute() or duration_seconds <= 0:
            raise MediaOutputError("video frame verification input is invalid")
        positions = {"start_anchor": 0.0, "end_anchor": max(0.0, duration_seconds - 0.05)}
        decoded = {}
        for name, position in positions.items():
            result = self.run("ffmpeg", ["-v", "error", "-ss", f"{position:.3f}", "-i", str(path),
                "-frames:v", "1", "-f", "null", "-"], timeout_seconds=30)
            decoded[name] = result.exit_code == 0
        return {"readable": all(decoded.values()), **decoded}

    @staticmethod
    def _minimal_environment() -> dict[str, str]:
        allowed = ("HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
        env = {key: os.environ[key] for key in allowed if key in os.environ}
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        return env


__all__ = [
    "DEFAULT_MAX_OUTPUT_BYTES",
    "MediaAdapter",
    "MediaAdapterError",
    "MediaOutputError",
    "MediaResult",
    "MediaTimeoutError",
    "SubprocessMediaRunner",
]
