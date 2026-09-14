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

from scripts.json_contracts import ContractValidationError, canonical_fingerprint, validate_contract


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


def _artifact_receipt(*, provider: str, path: Path, mime_type: str, kind: str,
                      rights_declared: Sequence[str], approved_root: str | None,
                      source: str, voice: str | None, model: str | None) -> dict[str, Any]:
    return {"provider": provider, "path": str(path), "sha256": _digest(path),
            "size_bytes": path.stat().st_size, "mime_type": mime_type,
            "provenance": {"kind": kind, "rights_declared": list(rights_declared),
                           "approved_root": approved_root, "source": source,
                           "voice": voice, "model": model, "source_voice_cloned": False}}


def _require_artifact(value: Mapping[str, Any], *, allowed_providers: set[str], required_right: str | None = None) -> dict[str, Any]:
    expected = {"provider", "path", "sha256", "size_bytes", "mime_type", "provenance"}
    if not isinstance(value, Mapping) or set(value) != expected or value.get("provider") not in allowed_providers:
        raise ContractValidationError("audio artifact receipt is incomplete or provider is forbidden")
    provenance = value.get("provenance")
    pfields = {"kind", "rights_declared", "approved_root", "source", "voice", "model", "source_voice_cloned"}
    if not isinstance(provenance, Mapping) or set(provenance) != pfields or provenance.get("source_voice_cloned") is not False:
        raise ContractValidationError("audio artifact provenance is incomplete")
    if required_right and required_right not in provenance.get("rights_declared", []):
        raise AudioRightsError(f"artifact lacks declared {required_right} rights")
    path = Path(str(value.get("path", "")))
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or _digest(path) != value.get("sha256") or path.stat().st_size != value.get("size_bytes"):
        raise ContractValidationError("audio artifact path, digest, or size binding is invalid")
    if provenance.get("kind") == "user_supplied":
        root = Path(str(provenance.get("approved_root", "")))
        if not root.is_absolute() or not path.resolve().is_relative_to(root.resolve()):
            raise AudioRightsError("user audio is outside its approved root")
    return json.loads(json.dumps(dict(value)))


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
            return _artifact_receipt(provider="macos-say", path=output, mime_type="audio/aiff",
                kind="new_narration", rights_declared=[], approved_root=None,
                source="rewritten_script", voice=voice, model="macos-say")
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
        return _artifact_receipt(provider="existing-audio", path=resolved, mime_type="audio/wav",
            kind="user_supplied", rights_declared=declared,
            approved_root=str(next(root for root in self._roots if resolved.is_relative_to(root))),
            source="user_supplied", voice=None, model=None)


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
        narration_value = None
        if narration is not None:
            narration_value = _require_artifact(narration, allowed_providers={"macos-say", "existing-audio"})
            if audio_policy == "full_redesign" and not (narration_value["provider"] == "macos-say" or
                    (narration_value["provenance"]["source"] == "user_new_narration" and "voice" in narration_value["provenance"]["rights_declared"])):
                raise AudioRightsError("full redesign narration must be trusted new narration")
        effects_value = [_require_artifact(item, allowed_providers={"existing-audio"}, required_right="effects") for item in effects]
        subtitles_value = [_require_artifact(item, allowed_providers={"subtitle-service"}) for item in subtitles]
        music_value = None
        if music is not None:
            raw = dict(music)
            intent = {"loop": raw.pop("loop", False), "trim_to_seconds": raw.pop("trim_to_seconds", None)}
            music_value = {**_require_artifact(raw, allowed_providers={"existing-audio"}, required_right="music"), "intent": intent}
        core = {
            "schema_version": "1.0", "version": "v001", "project_id": project_id,
            "design_fingerprint": design_fingerprint, "batch_fingerprint": batch_fingerprint,
            "creative_mode": creative_mode, "audio_policy": audio_policy,
            "target_duration_seconds": float(target_duration_seconds), "source_rights": dict(source_rights) if source_rights else None,
            "preserve": sorted(requested), "transcript": dict(transcript) if transcript else None,
            "rewritten_script": list(rewritten_script), "narration": narration_value,
            "music": music_value, "effects": effects_value, "subtitles": subtitles_value,
            "provenance": {"remote_services_used": False, "source_voice_cloned": False},
        }
        result = {**core, "plan_fingerprint": canonical_fingerprint(core)}
        validate_contract(result, "audio_plan.schema.json")
        return result


__all__ = ["AUDIO_POLICIES", "AudioPlanService", "AudioRightsError", "ExistingAudioProvider", "MacOSSayProvider", "NarrationProviderError"]
