"""Dependency-neutral argv-only bounded subprocess runner."""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Sequence


class BoundedProcessError(RuntimeError): pass
class BoundedProcessTimeout(BoundedProcessError): pass
class BoundedProcessOutput(BoundedProcessError): pass

@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int; stdout: str; stderr: str

def run_bounded(argv: Sequence[str], *, env: dict[str, str], timeout_seconds: int, stdout_cap: int, stderr_cap: int, pass_fds: Sequence[int] = ()) -> BoundedProcessResult:
    if not argv or not all(isinstance(arg, str) for arg in argv): raise BoundedProcessError("argv must be fixed strings")
    process = subprocess.Popen(list(argv), shell=False, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, close_fds=True, pass_fds=tuple(pass_fds))
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        if len(stdout) > stdout_cap or len(stderr) > stderr_cap: raise BoundedProcessOutput("process output exceeded cap")
        return BoundedProcessResult(process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.communicate()
        raise BoundedProcessTimeout("process timed out") from exc
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None: stream.close()
