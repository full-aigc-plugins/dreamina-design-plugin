"""Video request workflow for the Dreamina Design plugin (Task 4).

Mirrors :mod:`scripts.image_service` for the four video modes:

* ``text2video``
* ``image2video``
* ``frames2video``
* ``multimodal2video``

Responsibilities:

* Build a normalized video ``GenerationRequest`` from a current capability
  snapshot — never invent flags. Resolution, ratio, duration, and audio
  reference constraints are read from the snapshot only.
* Enforce the 4–30 second window required by the plugin plan; durations
  outside the snapshot's ``duration_min_seconds`` / ``duration_max_seconds``
  are rejected.
* For Seedance 2.5 (or any model that advertises ``web_prerequisite_required``)
  the very first submission is refused with
  :class:`VideoWebPrerequisiteRequired` until the user has acknowledged the
  web-console prerequisite. The flag is never silently bypassed.
* Bind submissions to an ``ApprovalReceipt`` whose ``request_fingerprint``
  matches the canonical SHA-256 of the request, including the references.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.dreamina_adapter import DreaminaResult


class VideoServiceError(Exception):
    """Base class for video workflow errors."""


class UnsupportedCapabilityError(VideoServiceError):
    """A requested model / resolution / ratio / mode is not in the snapshot."""


class DurationOutOfRangeError(VideoServiceError):
    """Duration is outside the [min, max] window advertised by the snapshot."""


class InvalidReferenceError(VideoServiceError):
    """Reference list violates mode-specific scope, role, or size rules."""


class MissingApprovalError(VideoServiceError):
    """A submission was attempted without an approval receipt."""


class ApprovalMismatchError(VideoServiceError):
    """The approval receipt's request_fingerprint does not match the request."""


class VideoWebPrerequisiteRequired(VideoServiceError):
    """The first-video web-console prerequisite has not been acknowledged."""


REFERENCE_LIMIT = 8
VIDEO_REFERENCE_ROLES = {"style", "subject", "frame", "audio", "reference"}
MODE_REQUIRED_REFERENCES = {
    "image2video": {"subject"},
    "frames2video": {"frame"},
}


@dataclass(frozen=True)
class _VideoModelSpec:
    name: str
    modes: frozenset[str]
    resolutions: frozenset[str]
    ratios: frozenset[str]
    duration_min_seconds: int
    duration_max_seconds: int
    web_prerequisite_required: bool
    audio_reference_max_seconds: int | None
    ratio_forbidden_modes: frozenset[str]
    max_references: int

    @classmethod
    def from_snapshot(cls, entry: Mapping[str, Any]) -> "_VideoModelSpec":
        audio_max = entry.get("audio_reference_max_seconds")
        return cls(
            name=str(entry["name"]),
            modes=frozenset(entry.get("modes", [])),
            resolutions=frozenset(entry.get("resolutions", [])),
            ratios=frozenset(entry.get("ratios", [])),
            duration_min_seconds=int(entry.get("duration_min_seconds", 4)),
            duration_max_seconds=int(entry.get("duration_max_seconds", 30)),
            web_prerequisite_required=bool(entry.get("web_prerequisite_required", False)),
            audio_reference_max_seconds=int(audio_max) if audio_max is not None else None,
            ratio_forbidden_modes=frozenset(entry.get("ratio_forbidden_modes", [])),
            max_references=int(entry.get("max_references", REFERENCE_LIMIT)),
        )


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_video_request_fingerprint(payload: Mapping[str, Any]) -> str:
    """SHA-256 hex digest over canonical JSON serialization."""
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


class VideoService:
    """Build and submit Dreamina video requests against a capability snapshot."""

    def __init__(self, snapshot: Mapping[str, Any], ledger_dir: Path) -> None:
        if "modes" not in snapshot:
            raise UnsupportedCapabilityError("snapshot missing modes")
        self._snapshot = snapshot
        self._modes = frozenset(snapshot.get("modes", []))
        self._ratios = frozenset(snapshot.get("ratios", []))
        self._video_resolutions = frozenset(snapshot.get("resolutions", {}).get("video", []))
        self._models = {
            entry["name"]: _VideoModelSpec.from_snapshot(entry)
            for entry in snapshot.get("models", [])
            if isinstance(entry, Mapping) and "name" in entry
        }
        self._ledger_dir = Path(ledger_dir)
        self._ledger_dir.mkdir(parents=True, exist_ok=True)
        self._web_prerequisite_acknowledged = False

    # ------------------------------------------------------------------
    # Web prerequisite acknowledgement
    # ------------------------------------------------------------------
    def record_web_prerequisite_acknowledgement(self) -> None:
        """Mark the web-console first-video prerequisite as acknowledged.

        The plugin plan forbids silent bypass. Callers must explicitly invoke
        this method after the user has performed the prerequisite in the
        Dreamina web console.
        """
        self._web_prerequisite_acknowledged = True
        flag_path = self._ledger_dir / "web_prerequisite.ack"
        flag_path.write_text("acknowledged\n", encoding="utf-8")

    def is_web_prerequisite_acknowledged(self) -> bool:
        return self._web_prerequisite_acknowledged

    # ------------------------------------------------------------------
    # Request construction
    # ------------------------------------------------------------------
    def build_request(
        self,
        *,
        mode: str,
        prompt: str,
        model: str,
        video_resolution: str | None = None,
        ratio: str | None = None,
        duration_seconds: int | None = None,
        references: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if mode not in self._modes:
            raise UnsupportedCapabilityError(f"mode not in snapshot: {mode}")
        spec = self._models.get(model)
        if spec is None:
            raise UnsupportedCapabilityError(f"unknown model: {model}")
        if mode not in spec.modes:
            raise UnsupportedCapabilityError(f"model {model} does not support mode {mode}")
        if not video_resolution:
            raise UnsupportedCapabilityError("video_resolution is required for video requests")
        advertised_resolutions = spec.resolutions or self._video_resolutions
        if video_resolution not in advertised_resolutions:
            raise UnsupportedCapabilityError(f"video_resolution not advertised: {video_resolution}")
        advertised_ratios = spec.ratios or self._ratios
        if ratio is not None and ratio not in advertised_ratios:
            raise UnsupportedCapabilityError(f"ratio not advertised: {ratio}")
        if ratio is not None and mode in spec.ratio_forbidden_modes:
            raise UnsupportedCapabilityError(
                f"ratio is not accepted for model {model} in mode {mode}"
            )
        effective_duration = duration_seconds if duration_seconds is not None else spec.duration_min_seconds
        if not (spec.duration_min_seconds <= effective_duration <= spec.duration_max_seconds):
            raise DurationOutOfRangeError(
                f"duration_seconds must be between {spec.duration_min_seconds} "
                f"and {spec.duration_max_seconds}, got {effective_duration}"
            )
        normalized_refs = self._validate_references(
            mode=mode,
            references=references or [],
            spec=spec,
        )

        request: dict[str, Any] = {
            "mode": mode,
            "prompt": prompt,
            "model": model,
            "video_resolution": video_resolution,
            "duration_seconds": effective_duration,
        }
        if ratio is not None:
            request["ratio"] = ratio
        if normalized_refs:
            request["references"] = normalized_refs
        return request

    @staticmethod
    def _validate_references(
        *,
        mode: str,
        references: list[Mapping[str, Any]],
        spec: _VideoModelSpec,
    ) -> list[dict[str, Any]]:
        required_roles = MODE_REQUIRED_REFERENCES.get(mode, set())
        if required_roles and not references:
            raise InvalidReferenceError(f"mode {mode} requires references with roles {sorted(required_roles)}")
        if not references:
            return []
        if len(references) > spec.max_references:
            raise InvalidReferenceError(f"too many references (max {spec.max_references})")
        normalized: list[dict[str, Any]] = []
        for ref in references:
            if not isinstance(ref, Mapping):
                raise InvalidReferenceError("reference must be an object")
            role = str(ref.get("role", "")).strip().lower()
            if role not in VIDEO_REFERENCE_ROLES:
                raise InvalidReferenceError(f"unknown reference role: {role}")
            path = ref.get("path")
            if not path:
                raise InvalidReferenceError("reference.path is required")
            entry: dict[str, Any] = {"path": str(path), "role": role}
            mime = ref.get("mime_type")
            if mime is not None:
                entry["mime_type"] = str(mime)
            size = ref.get("size_bytes")
            if size is not None:
                entry["size_bytes"] = int(size)
            audio_seconds = ref.get("duration_seconds")
            if role == "audio" and audio_seconds is not None:
                entry["duration_seconds"] = int(audio_seconds)
                if (
                    spec.audio_reference_max_seconds is not None
                    and int(audio_seconds) > spec.audio_reference_max_seconds
                ):
                    raise InvalidReferenceError(
                        f"audio reference duration {audio_seconds}s exceeds snapshot cap "
                        f"{spec.audio_reference_max_seconds}s"
                    )
            normalized.append(entry)
        if required_roles and not required_roles.intersection({r["role"] for r in normalized}):
            raise InvalidReferenceError(
                f"mode {mode} requires references with at least one of {sorted(required_roles)}"
            )
        if mode == "image2video" and (
            len(normalized) != 1 or normalized[0]["role"] != "subject"
        ):
            raise InvalidReferenceError("image2video requires exactly one subject reference")
        if mode == "frames2video" and (
            len(normalized) != 2 or any(r["role"] != "frame" for r in normalized)
        ):
            raise InvalidReferenceError("frames2video requires exactly two frame references")
        return normalized

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------
    def submit(
        self,
        request: Mapping[str, Any],
        *,
        adapter: Any,
        approval: Mapping[str, Any] | None,
        web_prerequisite_cleared: bool,
    ) -> dict[str, Any]:
        if approval is None:
            raise MissingApprovalError("approval receipt is required before submission")
        expected_fingerprint = build_video_request_fingerprint(dict(request))
        if approval.get("request_fingerprint") != expected_fingerprint:
            raise ApprovalMismatchError(
                f"approval request_fingerprint mismatch: expected {expected_fingerprint}"
            )
        spec = self._models[request["model"]]
        if spec.web_prerequisite_required and not self._web_prerequisite_acknowledged:
            # The caller can pass web_prerequisite_cleared=True to indicate the
            # web console step has been observed in the same session, but we
            # still require an in-process acknowledgement record.
            if not web_prerequisite_cleared or not self._web_prerequisite_acknowledged:
                raise VideoWebPrerequisiteRequired(
                    "first Dreamina video requires the user to acknowledge the web-console "
                    "prerequisite via record_web_prerequisite_acknowledgement(); "
                    "no silent bypass is permitted"
                )
        argv = self._request_to_argv(request)
        result: DreaminaResult = adapter.run(argv)
        payload = result.payload or {}
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if items is None and isinstance(payload, Mapping):
            items = [{"submit_id": payload.get("submit_id"), "index": 0}]
        return {
            "submit_id": result.submit_id,
            "items": list(items or []),
            "stderr": result.stderr,
        }

    @staticmethod
    def _request_to_argv(request: Mapping[str, Any]) -> list[str]:
        argv: list[str] = [
            str(request["mode"]),
            "--model_version", str(request["model"]),
            "--prompt", str(request["prompt"]),
            "--video_resolution", str(request["video_resolution"]),
            "--duration", str(request["duration_seconds"]),
        ]
        if "ratio" in request:
            argv.extend(["--ratio", str(request["ratio"])])
        references = list(request.get("references", []) or [])
        if request["mode"] == "image2video" and references:
            argv.extend(["--image", str(references[0]["path"])])
        elif request["mode"] == "frames2video":
            argv.extend(["--first", str(references[0]["path"])])
            argv.extend(["--last", str(references[1]["path"])])
        elif request["mode"] == "multimodal2video":
            flag_by_role = {"audio": "--audio", "frame": "--image", "style": "--image", "subject": "--image", "reference": "--video"}
            for ref in references:
                argv.extend([flag_by_role[ref["role"]], str(ref["path"])])
        return argv


__all__ = [
    "ApprovalMismatchError",
    "DurationOutOfRangeError",
    "InvalidReferenceError",
    "MissingApprovalError",
    "UnsupportedCapabilityError",
    "VideoService",
    "VideoWebPrerequisiteRequired",
    "build_video_request_fingerprint",
]
