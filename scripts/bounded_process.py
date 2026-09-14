"""Dependency-neutral argv-only bounded subprocess runner."""
from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Sequence


class BoundedProcessError(RuntimeError):
    """Invalid process request or bounded execution failure."""


class BoundedProcessTimeout(BoundedProcessError):
    """The process group exceeded its deadline."""


class BoundedProcessOutput(BoundedProcessError):
    """A pipe exceeded its byte budget."""


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: str
    stderr: str


def run_bounded(argv: Sequence[str], *, env: dict[str, str], timeout_seconds: float, stdout_cap: int, stderr_cap: int, pass_fds: Sequence[int] = ()) -> BoundedProcessResult:
    """Read both pipes incrementally; limits count bytes and include child lifetime.

    The process group is owned by this call, including descendants whose leader
    has already exited. Passed descriptors remain owned by the caller.
    """
    if isinstance(argv, (str, bytes)) or not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv):
        raise BoundedProcessError("argv must be fixed strings")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise BoundedProcessError("timeout must be positive and finite")
    if any(type(cap) is not int or cap < 0 for cap in (stdout_cap, stderr_cap)):
        raise BoundedProcessError("output caps must be nonnegative byte counts")
    deadline = time.monotonic() + timeout_seconds
    process = subprocess.Popen(list(argv), shell=False, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True, pass_fds=tuple(pass_fds))
    selector = None
    try:
        selector = selectors.DefaultSelector()
        buffers = [bytearray(), bytearray()]
        caps = (stdout_cap, stderr_cap)
        for index, stream in enumerate((process.stdout, process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoundedProcessTimeout("process timed out")
            for key, _ in selector.select(remaining):
                index = key.data
                # Read at most one byte past the cap, never accumulate excess.
                chunk = os.read(key.fd, min(65536, caps[index] - len(buffers[index]) + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                elif len(buffers[index]) + len(chunk) > caps[index]:
                    raise BoundedProcessOutput("process output exceeded cap")
                else:
                    buffers[index].extend(chunk)
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise BoundedProcessTimeout("process timed out") from exc
        return BoundedProcessResult(process.returncode, *(buffer.decode("utf-8", errors="replace") for buffer in buffers))
    finally:
        # Kill even when poll() says the leader exited: its children can remain.
        try:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        finally:
            try:
                if selector is not None:
                    selector.close()
            finally:
                try:
                    for stream in (process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
                finally:
                    process.wait()
