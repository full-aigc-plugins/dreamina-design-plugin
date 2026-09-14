"""Deterministic SRT/ASS rendering from approved script timing."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


class SubtitleTimelineError(ValueError):
    """Subtitle content or timing is unsafe or inconsistent."""


class SubtitleService:
    def __init__(self, *, max_text_length: int = 500) -> None:
        if not isinstance(max_text_length, int) or max_text_length <= 0:
            raise ValueError("max text length must be positive")
        self._max_text_length = max_text_length

    def from_source(self, cues: Sequence[Mapping[str, Any]], *, source: str) -> list[dict[str, Any]]:
        if source not in {"rewritten_script", "narration_timing"}:
            raise SubtitleTimelineError("subtitles require approved rewritten script or narration timing")
        return [dict(cue) for cue in cues]

    def render_srt(self, cues: Sequence[Mapping[str, Any]], *, target_duration_seconds: float | None = None) -> str:
        normalized = self._validate(cues, target_duration_seconds)
        lines = []
        for index, cue in enumerate(normalized, 1):
            text = cue["text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.extend([str(index), f"{self._srt_time(cue['start'])} --> {self._srt_time(cue['end'])}", text, ""])
        return "\n".join(lines)

    def render_ass(self, cues: Sequence[Mapping[str, Any]], *, target_duration_seconds: float | None = None) -> str:
        normalized = self._validate(cues, target_duration_seconds)
        header = "[Script Info]\nScriptType: v4.00+\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,Alignment\nStyle: Default,Arial,48,&H00FFFFFF,2\n[Events]\nFormat: Layer,Start,End,Style,Text\n"
        lines = []
        for cue in normalized:
            safe = cue["text"].replace("\\", "＼").replace("{", "｛").replace("}", "｝")
            lines.append(f"Dialogue: 0,{self._ass_time(cue['start'])},{self._ass_time(cue['end'])},Default,{safe}")
        return header + "\n".join(lines) + ("\n" if lines else "")

    def write_srt(self, cues: Sequence[Mapping[str, Any]], output_path: Path, *, target_duration_seconds: float) -> dict[str, Any]:
        return self._write(self.render_srt(cues, target_duration_seconds=target_duration_seconds), output_path, "srt")

    def write_ass(self, cues: Sequence[Mapping[str, Any]], output_path: Path, *, target_duration_seconds: float) -> dict[str, Any]:
        return self._write(self.render_ass(cues, target_duration_seconds=target_duration_seconds), output_path, "ass")

    def _validate(self, cues: Sequence[Mapping[str, Any]], target: float | None) -> list[dict[str, Any]]:
        if target is not None and (isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or target <= 0):
            raise SubtitleTimelineError("target duration must be positive and finite")
        result = []
        previous_end = 0.0
        for cue in cues:
            start, end, text = cue.get("start"), cue.get("end"), cue.get("text")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)):
                raise SubtitleTimelineError("cue timestamps must be finite numbers")
            clean = " ".join(text.split()) if isinstance(text, str) else ""
            if start < 0 or end <= start or start < previous_end or (target is not None and end > target) or not clean or len(clean) > self._max_text_length or "\x00" in clean:
                raise SubtitleTimelineError("cue timeline or text is invalid")
            result.append({"start": float(start), "end": float(end), "text": clean})
            previous_end = float(end)
        return result

    @staticmethod
    def _srt_time(value: float) -> str:
        millis = round(value * 1000); hours, rem = divmod(millis, 3600000); minutes, rem = divmod(rem, 60000); seconds, ms = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"

    @staticmethod
    def _ass_time(value: float) -> str:
        centis = round(value * 100); hours, rem = divmod(centis, 360000); minutes, rem = divmod(rem, 6000); seconds, cs = divmod(rem, 100)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"

    @staticmethod
    def _write(content: str, output_path: Path, format_name: str) -> dict[str, Any]:
        output = Path(output_path)
        if not output.is_absolute() or output.is_symlink() or not output.parent.is_dir():
            raise SubtitleTimelineError("subtitle output must be in an existing absolute directory")
        payload = content.encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=".subtitle-", dir=output.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, output); os.chmod(output, 0o600)
        finally:
            Path(temporary).unlink(missing_ok=True)
        from scripts.narration_service import _artifact_receipt
        return _artifact_receipt(provider="subtitle-service", path=output,
            mime_type="application/x-subrip" if format_name == "srt" else "text/x-ssa",
            kind="generated_subtitle", rights_declared=["subtitles"], approved_root=None,
            source="rewritten_script_or_narration_timing", voice=None, model=None)


__all__ = ["SubtitleService", "SubtitleTimelineError"]
