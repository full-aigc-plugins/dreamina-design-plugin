"""Local-only Whisper transcription through the enrolled media adapter."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.media_adapter import MediaAdapterError, MediaOutputError
from scripts.trusted_media_tools import TrustedMediaToolError


_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


class TranscriptionUnavailableError(RuntimeError):
    """No enrolled local ASR provider can produce trustworthy evidence."""

    status = "blocked"
    reason = "asr_provider_unavailable"


class WhisperCliProvider:
    """Invoke one fixed local Whisper model using JSON-only output."""

    def __init__(self, adapter: Any, model_path: Path, output_root: Path) -> None:
        self._adapter = adapter
        self._model = self._regular_absolute(model_path, "model")
        self._output_root = Path(output_root)
        if not self._output_root.is_absolute() or self._output_root.is_symlink():
            raise ValueError("Whisper output root must be an absolute non-symlink directory")
        self._output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._output_root, 0o700)
        self._output_root = self._output_root.resolve(strict=True)
        if stat.S_IMODE(self._output_root.stat().st_mode) != 0o700:
            raise ValueError("Whisper output root must be private mode 0700")

    def transcribe(self, audio_path: Path, *, language: str | None) -> list[dict[str, Any]]:
        source = self._regular_absolute(audio_path, "audio")
        if language is not None and _LANGUAGE.fullmatch(language) is None:
            raise ValueError("language must be a valid BCP-47 tag")
        argv = ["--model", str(self._model), "--output_format", "json", "--output_dir", str(self._output_root)]
        if language is not None:
            argv.extend(["--language", language])
        result = self._adapter.run("whisper", [*argv, str(source)], timeout_seconds=1800)
        if result.exit_code != 0:
            raise MediaOutputError(f"whisper failed with exit {result.exit_code}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise MediaOutputError("whisper did not emit one valid JSON document") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
            raise MediaOutputError("whisper JSON lacks segments")
        detected = payload.get("language")
        effective_language = language or detected
        if not isinstance(effective_language, str) or _LANGUAGE.fullmatch(effective_language) is None:
            raise MediaOutputError("whisper JSON lacks a valid language")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return self._normalize(payload["segments"], effective_language, digest)

    def _normalize(self, segments: Sequence[Mapping[str, Any]], language: str, digest: str) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        previous_end = 0.0
        for item in segments:
            if not isinstance(item, Mapping):
                raise MediaOutputError("whisper segment must be an object")
            start, end, confidence = item.get("start"), item.get("end"), item.get("confidence", item.get("avg_logprob"))
            text = item.get("text")
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (start, end, confidence)):
                raise ValueError("segment times and confidence must be finite numbers")
            if start < 0 or end <= start or start < previous_end or not isinstance(text, str) or not text.strip() or not -1 <= confidence <= 1:
                raise ValueError("segment timeline, text, or confidence is invalid")
            normalized.append({
                "start": float(start), "end": float(end), "language": language,
                "text": " ".join(text.split()), "confidence": float(confidence),
                "requires_review": confidence < 0.65, "provider": "whisper",
                "model": str(self._model), "artifact_sha256": digest,
            })
            previous_end = float(end)
        return normalized

    @staticmethod
    def _regular_absolute(path: Path, label: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"{label} path must be an absolute regular non-symlink file")
        return candidate.resolve(strict=True)


class TranscriptionService:
    """Expose explicit blocked results while allowing strict callers to fail closed."""

    def __init__(self, provider: WhisperCliProvider) -> None:
        self._provider = provider

    def transcribe(self, audio_path: Path, *, language: str | None) -> dict[str, Any]:
        try:
            segments = self._provider.transcribe(audio_path, language=language)
        except (MediaOutputError, ValueError, json.JSONDecodeError) as exc:
            return {"status": "degraded", "reason": "asr_output_invalid", "segments": [], "detail": type(exc).__name__}
        except (TrustedMediaToolError, OSError) as exc:
            return {"status": "blocked", "reason": "asr_provider_unavailable", "segments": [], "detail": type(exc).__name__}
        except MediaAdapterError as exc:
            return {"status": "blocked", "reason": "asr_provider_unavailable", "segments": [], "detail": type(exc).__name__}
        digest = hashlib.sha256(Path(audio_path).read_bytes()).hexdigest()
        return {"status": "complete", "provider": "whisper", "model": str(self._provider._model),
                "artifact_sha256": digest, "segments": segments}

    def require_transcript(self, audio_path: Path, *, language: str | None) -> list[dict[str, Any]]:
        result = self.transcribe(audio_path, language=language)
        if result["status"] != "complete":
            raise TranscriptionUnavailableError(result["reason"])
        return result["segments"]


__all__ = ["TranscriptionService", "TranscriptionUnavailableError", "WhisperCliProvider"]
