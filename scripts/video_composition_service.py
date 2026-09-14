"""Closed, deterministic local composition of already-approved shot artifacts."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


TRANSITIONS = frozenset({"cut", "crossfade", "dip_to_black"})
FPS_VALUES = frozenset({24, 25, 30})


class CompositionPlanError(RuntimeError):
    """The composition contract, input identity, or render result is unsafe."""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024): value.update(chunk)
    return value.hexdigest()


def _number(value: Any, name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= minimum:
        raise CompositionPlanError(f"{name} must be finite and greater than {minimum}")
    return float(value)


@dataclass(frozen=True)
class Clip:
    shot_id: str
    source_path: Path
    staged_path: Path
    sha256: str
    size_bytes: int
    duration_seconds: float


@dataclass(frozen=True)
class Transition:
    kind: str
    duration_seconds: float


@dataclass(frozen=True)
class MediaInput:
    role: str
    source_path: Path
    staged_path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class CompositionPlan:
    clips: tuple[Clip, ...]
    transitions: tuple[Transition, ...]
    media_inputs: tuple[MediaInput, ...]
    width: int
    height: int
    fps: int
    target_duration_seconds: float
    audio_plan: Mapping[str, Any] | None
    subtitle: Mapping[str, Any] | None
    subtitle_mode: str
    output_path: Path
    render_root: Path


class VideoCompositionService:
    """Build and execute one closed ffmpeg composition plan."""

    def __init__(self, media_adapter: Any, audio_plan_service: Any, private_render_root: Path,
                 *, timeout_seconds: int = 900) -> None:
        self._adapter = media_adapter; self._audio = audio_plan_service
        self._root = Path(private_render_root); self._timeout = timeout_seconds
        try: info = self._root.lstat()
        except OSError as exc: raise CompositionPlanError("render root is unavailable") from exc
        if not stat.S_ISDIR(info.st_mode) or self._root.is_symlink() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise CompositionPlanError("render root must be an owned private directory")

    def build_plan(self, *, required_shots: Sequence[str], clips: Sequence[Mapping[str, Any]],
                   transitions: Sequence[Mapping[str, Any]], width: int, height: int, fps: int,
                   target_duration_seconds: float, audio_plan: Mapping[str, Any] | None,
                   subtitle: Mapping[str, Any] | None, subtitle_mode: str, output_path: Path,
                   **forbidden: Any) -> CompositionPlan:
        if forbidden:
            raise CompositionPlanError("caller filter, codec, and extra argv fields are forbidden")
        if not required_shots or list(required_shots) != [item.get("shot_id") for item in clips] or len(set(required_shots)) != len(required_shots):
            raise CompositionPlanError("accepted shots must occur exactly once in required order")
        if isinstance(width, bool) or isinstance(height, bool) or not all(isinstance(v, int) for v in (width, height)) \
                or width % 2 or height % 2 or not 360 <= width <= 3840 or not 360 <= height <= 3840:
            raise CompositionPlanError("dimensions must be even and within 360..3840")
        if fps not in FPS_VALUES: raise CompositionPlanError("fps must be 24, 25, or 30")
        duration = _number(target_duration_seconds, "target duration")
        if len(transitions) != len(clips) - 1: raise CompositionPlanError("one transition is required between adjacent shots")
        staged = []
        for index, raw in enumerate(clips, 1):
            if set(raw) != {"shot_id", "path", "sha256", "size_bytes", "duration_seconds", "accepted"} or raw["accepted"] is not True:
                raise CompositionPlanError("clip receipt is incomplete or not accepted")
            source = Path(raw["path"])
            if not source.is_absolute() or source.is_symlink() or not source.is_file() or source.stat().st_uid != os.getuid():
                raise CompositionPlanError("clip must be an owned absolute regular file")
            clip_duration = _number(raw["duration_seconds"], "clip duration")
            if source.stat().st_size != raw["size_bytes"] or _digest(source) != raw["sha256"]: raise CompositionPlanError("clip identity changed")
            target = self._root / f"clip-{index:03d}{source.suffix.lower()}"
            if target.exists() or target.is_symlink(): raise CompositionPlanError("staged clip name already exists")
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                os.chmod(target, 0o600); shutil.copyfileobj(incoming, outgoing); outgoing.flush(); os.fsync(outgoing.fileno())
            if _digest(target) != raw["sha256"]: target.unlink(missing_ok=True); raise CompositionPlanError("staged clip digest mismatch")
            staged.append(Clip(raw["shot_id"], source, target, raw["sha256"], raw["size_bytes"], clip_duration))
        parsed = []
        for raw in transitions:
            if set(raw) != {"kind", "duration_seconds"} or raw["kind"] not in TRANSITIONS: raise CompositionPlanError("transition is not closed")
            seconds = raw["duration_seconds"]
            if raw["kind"] == "cut":
                if seconds != 0: raise CompositionPlanError("cut duration must be zero")
                seconds = 0.0
            else: seconds = _number(seconds, "transition duration")
            parsed.append(Transition(raw["kind"], seconds))
        calculated = sum(item.duration_seconds for item in staged) - sum(item.duration_seconds for item in parsed)
        if not math.isclose(calculated, duration, abs_tol=1 / fps): raise CompositionPlanError("target duration disagrees with transition math")
        if subtitle_mode not in {"none", "mux", "burn"} or (subtitle_mode == "none") != (subtitle is None):
            raise CompositionPlanError("subtitle mode and artifact are inconsistent")
        output = Path(output_path)
        if not output.is_absolute() or output.suffix.lower() != ".mp4" or output.exists() or output.is_symlink(): raise CompositionPlanError("output must be a new absolute MP4 path")
        media_inputs = []
        candidates = []
        if audio_plan:
            candidates += ([audio_plan.get("narration")] if audio_plan.get("narration") else [])
            candidates += ([audio_plan.get("music")] if audio_plan.get("music") else [])
            candidates += list(audio_plan.get("effects", []))
        if subtitle: candidates.append(subtitle)
        for index, artifact in enumerate(candidates, 1):
            source = Path(str(artifact.get("path", "")))
            digest = artifact.get("sha256"); size = artifact.get("size_bytes")
            if not source.is_absolute() or source.is_symlink() or not source.is_file() or source.stat().st_size != size or _digest(source) != digest:
                raise CompositionPlanError("audio or subtitle artifact identity is invalid")
            target = self._root / f"media-{index:03d}{source.suffix.lower()}"
            if target.exists() or target.is_symlink(): raise CompositionPlanError("staged media name already exists")
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                os.chmod(target, 0o600); shutil.copyfileobj(incoming, outgoing); outgoing.flush(); os.fsync(outgoing.fileno())
            media_inputs.append(MediaInput(str(artifact.get("artifact_role")), source, target, digest, size))
        return CompositionPlan(tuple(staged), tuple(parsed), tuple(media_inputs), width, height, fps, duration, audio_plan, subtitle,
                               subtitle_mode, output, self._root)

    def build_ffmpeg_argv(self, plan: CompositionPlan, *, output_path: Path | None = None) -> list[str]:
        argv = ["-hide_banner", "-nostdin", "-y"]
        for clip in plan.clips: argv += ["-i", str(clip.staged_path)]
        for item in plan.media_inputs: argv += ["-i", str(item.staged_path)]
        filters = []
        for index in range(len(plan.clips)):
            filters.append(f"[{index}:v]fps={plan.fps},scale={plan.width}:{plan.height}:force_original_aspect_ratio=decrease,pad={plan.width}:{plan.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v{index}]")
        current = "v0"; elapsed = plan.clips[0].duration_seconds
        for index, transition in enumerate(plan.transitions, 1):
            if transition.kind == "cut":
                filters.append(f"[{current}][v{index}]concat=n=2:v=1:a=0[vx{index}]")
            else:
                name = "fade" if transition.kind == "crossfade" else "fadeblack"
                offset = elapsed - transition.duration_seconds
                filters.append(f"[{current}][v{index}]xfade=transition={name}:duration={transition.duration_seconds:g}:offset={offset:g}[vx{index}]")
            current = f"vx{index}"; elapsed += plan.clips[index].duration_seconds - transition.duration_seconds
        subtitle_input = next((i for i, item in enumerate(plan.media_inputs, len(plan.clips)) if item.role.startswith("subtitle_")), None)
        if plan.subtitle_mode == "burn" and subtitle_input is not None:
            filters.append(f"[{current}]subtitles={plan.media_inputs[subtitle_input-len(plan.clips)].staged_path}[vout]"); current = "vout"
        audio_indexes = [(i, item) for i, item in enumerate(plan.media_inputs, len(plan.clips)) if not item.role.startswith("subtitle_")]
        audio_label = None
        if audio_indexes and plan.audio_plan and plan.audio_plan.get("audio_policy") != "silent":
            labels = []
            for pos, (index, item) in enumerate(audio_indexes):
                volume = "0.25" if item.role == "music" else ("0.7" if item.role == "effect" else "1.0")
                filters.append(f"[{index}:a]atrim=0:{plan.target_duration_seconds:g},asetpts=PTS-STARTPTS,volume={volume}[a{pos}]"); labels.append(f"[a{pos}]")
            filters.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0,loudnorm=I=-16:LRA=11:TP=-1.5,afade=t=out:st={max(0, plan.target_duration_seconds-.25):g}:d=.25[aout]")
            audio_label = "aout"
        argv += ["-filter_complex", ";".join(filters), "-map", f"[{current}]"]
        if audio_label: argv += ["-map", f"[{audio_label}]", "-c:a", "aac", "-profile:a", "aac_low", "-ar", "48000"]
        elif plan.subtitle_mode == "mux" and subtitle_input is not None: argv += ["-map", str(subtitle_input), "-c:s", "mov_text", "-an"]
        else: argv += ["-an"]
        argv += ["-r", str(plan.fps), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-t", f"{plan.target_duration_seconds:g}", str(output_path or plan.output_path)]
        return argv

    def compose(self, plan: CompositionPlan) -> Path:
        if plan.output_path.exists(): raise CompositionPlanError("output already exists")
        for clip in plan.clips:
            for path in (clip.source_path, clip.staged_path):
                if path.is_symlink() or not path.is_file() or path.stat().st_size != clip.size_bytes or _digest(path) != clip.sha256:
                    raise CompositionPlanError("composition input identity changed")
        for item in plan.media_inputs:
            for path in (item.source_path, item.staged_path):
                if path.is_symlink() or not path.is_file() or path.stat().st_size != item.size_bytes or _digest(path) != item.sha256:
                    raise CompositionPlanError("composition media identity changed")
        if plan.audio_plan is not None: self._audio.verify_for_use(plan.audio_plan)
        descriptor, temp_name = tempfile.mkstemp(prefix=".composition-", suffix=".mp4", dir=plan.output_path.parent)
        os.close(descriptor); temp = Path(temp_name); temp.unlink()
        try:
            result = self._adapter.run("ffmpeg", self.build_ffmpeg_argv(plan, output_path=temp), timeout_seconds=self._timeout)
            if result.exit_code != 0 or not temp.is_file() or temp.is_symlink() or temp.stat().st_size <= 0: raise CompositionPlanError("ffmpeg composition failed")
            with temp.open("rb") as handle: os.fsync(handle.fileno())
            os.link(temp, plan.output_path); temp.unlink(); os.chmod(plan.output_path, 0o600)
            directory = os.open(plan.output_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)); os.fsync(directory); os.close(directory)
            return plan.output_path
        finally:
            temp.unlink(missing_ok=True)


__all__ = ["Clip", "CompositionPlan", "CompositionPlanError", "FPS_VALUES", "TRANSITIONS", "Transition", "VideoCompositionService"]
