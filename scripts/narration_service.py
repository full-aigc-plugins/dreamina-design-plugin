"""Rights-aware audio planning and local narration providers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from scripts.json_contracts import canonical_fingerprint


AUDIO_POLICIES = frozenset({"full_redesign", "preserve_authorized_audio", "subtitles_only", "silent"})
AUDIO_CLASSES = frozenset({"voice", "dialogue", "music", "effects"})
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


class AudioRightsError(PermissionError):
    """Audio reuse or supplied media lacks exact rights evidence."""


class NarrationProviderError(RuntimeError):
    """Narration request or artifact is invalid."""


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


class MacOSSayProvider:
    """Synthesize new narration with an enrolled /usr/bin/say-compatible tool."""

    def __init__(self, adapter: Any, private_root: Path, *, voices: Iterable[str]) -> None:
        self._adapter = adapter
        self._root = Path(private_root)
        self._voices = frozenset(voices)
        if not self._root.is_absolute() or self._root.is_symlink() or not self._root.is_dir():
            raise NarrationProviderError("narration root must be an absolute private directory")
        os.chmod(self._root, 0o700)
        if not self._voices or any(not isinstance(v, str) or not v.strip() for v in self._voices):
            raise NarrationProviderError("discovered voice allowlist is invalid")

    def synthesize(self, cues: Sequence[Mapping[str, Any]], *, voice: str, output_path: Path) -> dict[str, Any]:
        if voice not in self._voices:
            raise NarrationProviderError("voice is not in the discovered allowlist")
        output = Path(output_path)
        if not output.is_absolute() or output.is_symlink() or output.parent.resolve(strict=True) != self._root.resolve(strict=True):
            raise NarrationProviderError("narration output must be directly inside the private root")
        texts = []
        for cue in cues:
            text = cue.get("text")
            if not isinstance(text, str) or not text.strip() or "\x00" in text:
                raise NarrationProviderError("narration cue text is invalid")
            texts.append(" ".join(text.split()))
        descriptor, name = tempfile.mkstemp(prefix=".narration-", suffix=".txt", dir=self._root)
        script = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(texts) + ("\n" if texts else ""))
                handle.flush(); os.fsync(handle.fileno())
            result = self._adapter.run("narration", ["-v", voice, "-f", str(script), "-o", str(output)], timeout_seconds=300)
            if result.exit_code != 0 or not output.is_file() or output.is_symlink():
                raise NarrationProviderError("local narration generation failed")
            os.chmod(output, 0o600)
            return {"provider": "macos-say", "voice": voice, "path": str(output), "sha256": _digest(output), "provenance": {"kind": "new_narration", "source_voice_cloned": False}}
        finally:
            script.unlink(missing_ok=True)


class ExistingAudioProvider:
    """Accept an immutable, rights-declared user track from approved roots."""

    def __init__(self, approved_roots: Iterable[Path]) -> None:
        roots = []
        for root in approved_roots:
            candidate = Path(root)
            if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
                raise AudioRightsError("approved audio roots are invalid")
            roots.append(candidate.resolve(strict=True))
        if not roots:
            raise AudioRightsError("at least one approved audio root is required")
        self._roots = tuple(roots)

    def accept(self, path: Path, *, expected_sha256: str, rights: Mapping[str, bool]) -> dict[str, Any]:
        source = Path(path)
        if not source.is_absolute() or source.is_symlink() or not source.is_file():
            raise AudioRightsError("audio must be an absolute regular non-symlink file")
        resolved = source.resolve(strict=True)
        if not any(resolved.is_relative_to(root) for root in self._roots):
            raise AudioRightsError("audio is outside approved roots")
        declared = sorted(key for key, value in rights.items() if key in AUDIO_CLASSES and value is True)
        if set(rights).difference(AUDIO_CLASSES) or not declared or _DIGEST.fullmatch(expected_sha256) is None or _digest(resolved) != expected_sha256:
            raise AudioRightsError("audio digest, provenance, or rights declaration is invalid")
        return {"provider": "existing-audio", "path": str(resolved), "sha256": expected_sha256, "provenance": {"kind": "user_supplied", "approved_root": str(next(root for root in self._roots if resolved.is_relative_to(root))), "rights_declared": declared}}


class AudioPlanService:
    """Build a closed, fingerprinted handoff for Task 12 composition."""

    def create_plan(self, *, project_id: str, design_fingerprint: str, batch_fingerprint: str,
                    creative_mode: str, audio_policy: str, source_rights: Mapping[str, Any] | None,
                    transcript: Mapping[str, Any] | None, rewritten_script: Sequence[Mapping[str, Any]],
                    narration: Mapping[str, Any] | None, music: Mapping[str, Any] | None,
                    effects: Sequence[Mapping[str, Any]], subtitles: Sequence[Mapping[str, Any]],
                    target_duration_seconds: float, preserve: Sequence[str] = ()) -> dict[str, Any]:
        if audio_policy not in AUDIO_POLICIES or creative_mode not in {"authorized_replication", "original_redesign"}:
            raise ValueError("creative mode or audio policy is unsupported")
        if isinstance(target_duration_seconds, bool) or not isinstance(target_duration_seconds, (int, float)) or target_duration_seconds <= 0:
            raise ValueError("target duration must be positive")
        requested = set(preserve)
        if not requested <= AUDIO_CLASSES or len(requested) != len(preserve):
            raise AudioRightsError("preserved audio classes must be closed and unique")
        if creative_mode == "original_redesign" and requested.intersection({"voice", "dialogue", "music"}):
            raise AudioRightsError("original redesign cannot reuse source voice, dialogue, or music")
        if audio_policy == "preserve_authorized_audio":
            allowed = set(source_rights.get("allowed_reuse", [])) if source_rights else set()
            if creative_mode != "authorized_replication" or not requested or not requested <= allowed:
                raise AudioRightsError("rights receipt does not cover every requested audio class")
        elif requested:
            raise AudioRightsError("source audio reuse requires preserve_authorized_audio")
        if audio_policy == "full_redesign" and (not rewritten_script or narration is None):
            raise ValueError("full redesign requires a rewritten script and new narration")
        if audio_policy in {"subtitles_only", "silent"} and (narration is not None or music is not None or effects):
            raise ValueError("subtitle-only or silent plans cannot invent audio")
        if audio_policy == "silent" and (rewritten_script or subtitles):
            raise ValueError("silent plans cannot contain narration or subtitles")
        music_value = None
        if music is not None:
            music_value = {"sha256": music.get("sha256"), "intent": {"loop": music.get("loop", False), "trim_to_seconds": music.get("trim_to_seconds")}}
        core = {
            "schema_version": "1.0", "version": "v001", "project_id": project_id,
            "design_fingerprint": design_fingerprint, "batch_fingerprint": batch_fingerprint,
            "creative_mode": creative_mode, "audio_policy": audio_policy,
            "target_duration_seconds": float(target_duration_seconds), "source_rights": dict(source_rights) if source_rights else None,
            "preserve": sorted(requested), "transcript": dict(transcript) if transcript else None,
            "rewritten_script": list(rewritten_script), "narration": dict(narration) if narration else None,
            "music": music_value, "effects": list(effects), "subtitles": list(subtitles),
            "provenance": {"remote_services_used": False, "source_voice_cloned": False},
        }
        return {**core, "plan_fingerprint": canonical_fingerprint(core)}


__all__ = ["AUDIO_POLICIES", "AudioPlanService", "AudioRightsError", "ExistingAudioProvider", "MacOSSayProvider", "NarrationProviderError"]
