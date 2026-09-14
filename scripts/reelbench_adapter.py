"""Guarded, argv-only bridge to the pinned ReelBench video-shots script."""

from __future__ import annotations

import math
import os
import re
import json
import shutil
import tempfile
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.bounded_process import BoundedProcessError, BoundedProcessResult, run_bounded
from scripts.reelbench_contracts import REELBENCH_VALIDATE_GATES
from scripts.trusted_media_tools import TrustedExecutable, TrustedMediaToolStore


MAX_STDOUT_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 1024 * 1024
MAX_ACTION_SECONDS = 120.0
MAX_TITLE_CHARS = 512
_GATE_MARK = {"✅": "PASS", "❌": "FAIL", "⊘": "SKIPPED"}
_GATE_LINE = re.compile(r"^(?P<mark>[✅❌⊘])\s+(?P<evidence>.+)$")
_ENGLISH_GATE_LABELS = {
    "timeline": "Timeline is continuous", "duration": "Durations add up", "numbering": "Shot numbering",
    "size": "Shot size vocabulary", "category": "Category vocabulary", "camera": "Camera vocabulary",
    "transition": "Transition vocabulary", "frame-text": "Frame description is checkable",
    "dedup": "No duplicate descriptions", "subjects": "Subjects reconcile with cast",
    "category-evidence": "Categories carry evidence", "motion": "Camera vs. measured motion",
    "boundary": "Boundaries come from detection", "frames": "Keyframes present",
    "rhythm": "Rhythm annotation is checkable",
}


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
    tool_identities: tuple[dict[str, str | int], ...] = ()


class ReelBenchAdapter:
    """Run unchanged ``video-shots.mjs`` only against one private project root."""

    def __init__(
        self,
        *,
        project_root: Path,
        shots_script: Path,
        tools: Mapping[str, TrustedExecutable],
        tool_store: TrustedMediaToolStore | None = None,
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
        if runner is run_bounded and tool_store is None:
            raise ValueError("real ReelBench execution requires descriptor-verified trusted media tools")
        self._tools = dict(tools)
        self._tool_store = tool_store
        self._runner = runner
        self._ENGLISH_GATE_LABELS = _ENGLISH_GATE_LABELS

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
        result = self._run("validate", [str(shots_path), "--track", str(track_path), "--frames", str(frames_path), "--lang", "en"], allow_failure=True)
        gates = self._parse_gates(result.stdout)
        failed = any(gate["status"] == "FAIL" for gate in gates)
        if result.returncode not in {0, 1} or (result.returncode == 0 and failed) or (result.returncode == 1 and not failed):
            raise ReelBenchAdapterError(f"validate exited unexpectedly: {result.returncode}")
        return ReelBenchAdapterResult(result.action, result.argv, result.shell, result.stdout, result.stderr, result.returncode, gates, result.tool_identities)

    def render(self, *, shots: Path, track: Path, frames_dir: Path, source: Path, mode: str) -> ReelBenchAdapterResult:
        """Render the unchanged Markdown or offline HTML report to bounded stdout."""
        if mode not in {"md", "html"}:
            raise ValueError("render mode must be md or html")
        shots_path, track_path, frames_path, source_path = self._paths(shots, track, frames_dir, source)
        return self._run("render", [str(shots_path), f"--{mode}", "--track", str(track_path), "--frames", str(frames_path), "--video", str(source_path)])

    def _run(self, action: str, rest: Sequence[str], *, allow_failure: bool = False) -> ReelBenchAdapterResult:
        staged_root: Path | None = None
        staged_tools = None
        workspace_fd: int | None = None
        tools = self._tools
        script = self._shots_script
        environment = self._environment()
        if self._tool_store is not None:
            staged_tools = self._tool_store.load_required(("node", "ffmpeg", "ffprobe"))
            staged_root = Path(tempfile.mkdtemp(prefix=".reelbench-exec-", dir=self._project_root))
            os.chmod(staged_root, 0o700)
            script = self._verify_and_stage_script(staged_root)
            bin_dir = staged_root / "bin"
            bin_dir.mkdir(mode=0o700)
            for kind in ("ffmpeg", "ffprobe"):
                os.link(staged_tools[kind].staged_path, bin_dir / kind, follow_symlinks=False)
                os.chmod(bin_dir / kind, 0o500)
            environment = {"PATH": str(bin_dir), "LANG": "C", "LC_ALL": "C"}
            tools = {kind: self._identity_from_path(kind, Path(staged_tools[kind].source_path)) for kind in staged_tools}
            source_index = 0 if action == "seed" else (rest.index("--video") + 1 if "--video" in rest else None)
            if source_index is not None:
                private_source = self._private_source_copy(Path(rest[source_index]), staged_root)
                rest = [str(private_source) if index == source_index else value for index, value in enumerate(rest)]
            workspace_fd = os.open(staged_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
            helper = Path(__file__).with_name("reelbench_exec_helper.py")
            argv = [sys.executable, str(helper), str(workspace_fd), str(staged_tools["node"].staged_path), str(script), action, *rest]
        else:
            argv = [tools["node"].source_path, str(script), action, *rest]
        try:
            result = self._runner(
                argv,
                env=environment,
                timeout_seconds=MAX_ACTION_SECONDS,
                stdout_cap=MAX_STDOUT_BYTES,
                stderr_cap=MAX_STDERR_BYTES,
                pass_fds=(workspace_fd,) if workspace_fd is not None else (),
            )
        except (BoundedProcessError, OSError) as exc:
            raise ReelBenchAdapterError(f"{action} did not complete safely") from exc
        finally:
            if staged_tools is not None:
                for tool in staged_tools.values():
                    self._tool_store.release(tool)
            if staged_root is not None:
                shutil.rmtree(staged_root, ignore_errors=True)
            if workspace_fd is not None:
                os.close(workspace_fd)
        if not isinstance(result, BoundedProcessResult):
            raise ReelBenchAdapterError("bounded runner returned an invalid result")
        if result.returncode != 0 and not allow_failure:
            raise ReelBenchAdapterError(f"{action} failed with exit {result.returncode}")
        identities = tuple({"kind": kind, **tools[kind].to_record()} for kind in sorted(tools))
        return ReelBenchAdapterResult(action, list(argv), False, result.stdout, result.stderr, result.returncode, (), identities)

    def _verify_and_stage_script(self, root: Path) -> Path:
        lock = self._shots_script.parents[3] / "upstream" / "reelbench.lock.json"
        try:
            expected = json.loads(lock.read_text(encoding="utf-8"))["files"]["video-shots/scripts/video-shots.mjs"]["packaged_sha256"]
            descriptor = os.open(self._shots_script, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise ReelBenchAdapterError("pinned ReelBench script cannot be verified") from exc
        try:
            content = bytearray()
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                content.extend(chunk); digest.update(chunk)
            if digest.hexdigest() != expected:
                raise ReelBenchAdapterError("packaged ReelBench script differs from its lock")
        finally:
            os.close(descriptor)
        target = root / "video-shots.mjs"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o500)
        try:
            os.write(fd, content); os.fsync(fd)
        finally:
            os.close(fd)
        return target

    @staticmethod
    def _identity_from_path(kind: str, path: Path) -> TrustedExecutable:
        info = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return TrustedExecutable(kind, str(path), info.st_uid, info.st_mode & 0o777, info.st_dev, info.st_ino, info.st_size, digest)

    @staticmethod
    def _private_source_copy(source: Path, workspace: Path) -> Path:
        """Publish source bytes under the pinned workspace; Node children use no caller pathname."""
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            digest = hashlib.sha256(); chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk); chunks.append(chunk)
        finally:
            os.close(descriptor)
        source_root = workspace / "source"; source_root.mkdir(mode=0o700)
        target = source_root / digest.hexdigest()
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
        try:
            for chunk in chunks:
                os.write(fd, chunk)
            os.fsync(fd)
        finally:
            os.close(fd)
        directory = os.open(source_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try: os.fsync(directory)
        finally: os.close(directory)
        return target

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
        found: list[dict[str, str]] = []
        for line in stdout.splitlines():
            matched = _GATE_LINE.fullmatch(line)
            if matched is not None:
                found.append({"status": _GATE_MARK[matched.group("mark")], "evidence": matched.group("evidence")[:2048]})
        if len(found) != len(REELBENCH_VALIDATE_GATES):
            raise ReelBenchAdapterError("validate output did not contain exactly 15 gate lines")
        receipts = tuple({"name": name, **entry} for name, entry in zip(REELBENCH_VALIDATE_GATES, found, strict=True))
        if any(not (entry["evidence"] == _ENGLISH_GATE_LABELS[entry["name"]] or entry["evidence"].startswith(_ENGLISH_GATE_LABELS[entry["name"]] + "　")) for entry in receipts):
            raise ReelBenchAdapterError("validate gate labels are unknown, duplicated, or reordered")
        return receipts


__all__ = ["ReelBenchAdapter", "ReelBenchAdapterError", "ReelBenchAdapterResult"]
