"""Guarded, argv-only bridge to the pinned ReelBench video-shots script."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.bounded_process import BoundedProcessError, BoundedProcessResult, run_bounded
from scripts.reelbench_contracts import REELBENCH_VALIDATE_GATES
from scripts.trusted_media_tools import TrustedExecutable


MAX_STDOUT_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 1024 * 1024
MAX_ACTION_SECONDS = 120.0
MAX_TITLE_CHARS = 512
_GATE_MARK = {"✅": "PASS", "❌": "FAIL", "⊘": "SKIPPED"}
_GATE_LINE = re.compile(r"^(?P<mark>[✅❌⊘])\s+(?P<evidence>.+)$")


class ReelBenchAdapterError(RuntimeError):
    """The pinned ReelBench script could not be run or returned unsafe output."""


@dataclass(frozen=True)
class ReelBenchAdapterResult:
    """Bounded output and immutable argv evidence from one upstream invocation."""

    action: str
    argv: list[str]
    shell: bool
    stdout: str
    stderr: str
    returncode: int
    gates: tuple[dict[str, str], ...] = ()


class ReelBenchAdapter:
    """Run unchanged ``video-shots.mjs`` only against one private project root."""

    def __init__(
        self,
        *,
        project_root: Path,
        shots_script: Path,
        tools: Mapping[str, TrustedExecutable],
        runner: Callable[..., BoundedProcessResult] = run_bounded,
    ) -> None:
        self._project_root = Path(project_root).resolve(strict=True)
        if not self._project_root.is_dir() or self._project_root.is_symlink():
            raise ValueError("project root must be a private non-symlink directory")
        self._shots_script = Path(shots_script).resolve(strict=True)
        if not self._shots_script.is_file() or self._shots_script.is_symlink() or self._shots_script.name != "video-shots.mjs":
            raise ValueError("shots script must be the packaged pinned entrypoint")
        if set(tools) != {"node", "ffmpeg", "ffprobe"}:
            raise ValueError("ReelBench requires exactly trusted node, ffmpeg, and ffprobe tools")
        if any(tool.kind != kind or not Path(tool.source_path).is_absolute() or tool.size_bytes < 1 for kind, tool in tools.items()):
            raise ValueError("trusted ReelBench tool identities are invalid")
        self._tools = dict(tools)
        self._runner = runner

    @property
    def tool_identities(self) -> list[dict[str, str | int]]:
        """Return closed executable identities for a versioned evidence receipt."""
        return [{"kind": kind, **self._tools[kind].to_record()} for kind in sorted(self._tools)]

    def seed(self, *, source: Path, shots: Path, track: Path, threshold: float, title: str) -> ReelBenchAdapterResult:
        """Run the unchanged seed command with fixed flag ordering."""
        self._bounded_number("threshold", threshold, 0.05, 0.80)
        self._bounded_text("title", title, MAX_TITLE_CHARS)
        source_path, shots_path, track_path = self._paths(source, shots, track)
        return self._run(
            "seed",
            [str(source_path), "--threshold", self._number(threshold), "--min", "0.30", "--track", str(track_path), "--title", title],
        )

    def frames(self, *, shots: Path, source: Path, frames_dir: Path) -> ReelBenchAdapterResult:
        """Extract bounded private keyframes using the pinned upstream command."""
        shots_path, source_path, frames_path = self._paths(shots, source, frames_dir)
        return self._run("frames", [str(shots_path), "--video", str(source_path), "--dir", str(frames_path), "--width", "480"])

    def sheet(self, *, shots: Path, frames_dir: Path, sheets_dir: Path, pick: str) -> ReelBenchAdapterResult:
        """Build one deterministic A or B contact-sheet collection."""
        if pick not in {"a", "b"}:
            raise ValueError("sheet pick must be a or b")
        shots_path, frames_path, sheets_path = self._paths(shots, frames_dir, sheets_dir)
        return self._run("sheet", [str(shots_path), "--dir", str(frames_path), "--out", str(sheets_path), "--pick", pick, "--cols", "5", "--rows", "5"])

    def validate(self, *, shots: Path, track: Path, frames_dir: Path) -> ReelBenchAdapterResult:
        """Run upstream gates without collapsing PASS, FAIL, or SKIPPED semantics."""
        shots_path, track_path, frames_path = self._paths(shots, track, frames_dir)
        result = self._run("validate", [str(shots_path), "--track", str(track_path), "--frames", str(frames_path)], allow_failure=True)
        gates = self._parse_gates(result.stdout)
        if result.returncode not in {0, 1}:
            raise ReelBenchAdapterError(f"validate exited unexpectedly: {result.returncode}")
        return ReelBenchAdapterResult(result.action, result.argv, result.shell, result.stdout, result.stderr, result.returncode, gates)

    def render(self, *, shots: Path, track: Path, frames_dir: Path, source: Path, mode: str) -> ReelBenchAdapterResult:
        """Render the unchanged Markdown or offline HTML report to bounded stdout."""
        if mode not in {"md", "html"}:
            raise ValueError("render mode must be md or html")
        shots_path, track_path, frames_path, source_path = self._paths(shots, track, frames_dir, source)
        return self._run("render", [str(shots_path), f"--{mode}", "--track", str(track_path), "--frames", str(frames_path), "--video", str(source_path)])

    def _run(self, action: str, rest: Sequence[str], *, allow_failure: bool = False) -> ReelBenchAdapterResult:
        argv = [self._tools["node"].source_path, str(self._shots_script), action, *rest]
        try:
            result = self._runner(
                argv,
                env=self._environment(),
                timeout_seconds=MAX_ACTION_SECONDS,
                stdout_cap=MAX_STDOUT_BYTES,
                stderr_cap=MAX_STDERR_BYTES,
            )
        except (BoundedProcessError, OSError) as exc:
            raise ReelBenchAdapterError(f"{action} did not complete safely") from exc
        if not isinstance(result, BoundedProcessResult):
            raise ReelBenchAdapterError("bounded runner returned an invalid result")
        if result.returncode != 0 and not allow_failure:
            raise ReelBenchAdapterError(f"{action} failed with exit {result.returncode}")
        return ReelBenchAdapterResult(action, list(argv), False, result.stdout, result.stderr, result.returncode)

    def _environment(self) -> dict[str, str]:
        executable_dirs = [str(Path(self._tools[kind].source_path).parent) for kind in ("ffmpeg", "ffprobe")]
        return {"PATH": os.pathsep.join([*dict.fromkeys(executable_dirs), "/usr/bin", "/bin"]), "LANG": "C", "LC_ALL": "C"}

    def _paths(self, *values: Path) -> tuple[Path, ...]:
        return tuple(self._project_path(value) for value in values)

    def _project_path(self, value: Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("ReelBench paths must be absolute")
        candidate = path.resolve(strict=False)
        try:
            candidate.relative_to(self._project_root)
        except ValueError as exc:
            raise ValueError("ReelBench paths must be service-created project paths") from exc
        if path.is_symlink():
            raise ValueError("ReelBench paths may not be symlinks")
        return candidate

    @staticmethod
    def _bounded_number(label: str, value: float, lower: float, upper: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= float(value) <= upper:
            raise ValueError(f"{label} must be finite and between {lower} and {upper}")

    @staticmethod
    def _bounded_text(label: str, value: str, maximum: int) -> None:
        if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
            raise ValueError(f"{label} is invalid")

    @staticmethod
    def _number(value: float) -> str:
        return f"{float(value):.2f}"

    @staticmethod
    def _parse_gates(stdout: str) -> tuple[dict[str, str], ...]:
        found = []
        for line in stdout.splitlines():
            matched = _GATE_LINE.fullmatch(line)
            if matched is not None:
                found.append((_GATE_MARK[matched.group("mark")], matched.group("evidence")[:2048]))
        if len(found) != len(REELBENCH_VALIDATE_GATES):
            raise ReelBenchAdapterError("validate output did not contain exactly 15 gate lines")
        return tuple({"name": name, "status": status, "evidence": evidence} for name, (status, evidence) in zip(REELBENCH_VALIDATE_GATES, found, strict=True))


__all__ = ["ReelBenchAdapter", "ReelBenchAdapterError", "ReelBenchAdapterResult"]
