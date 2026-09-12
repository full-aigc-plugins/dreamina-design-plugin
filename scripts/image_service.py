"""Image request workflow for the Dreamina Design plugin (Task 3).

This module turns a user intent into a validated ``GenerationRequest``
plus a single, approval-bound batch submission against the installed
``dreamina`` CLI. Every parameter it emits is grounded in the current
capability snapshot — no hard-coded catalogs, no invented flags.

Responsibilities:

* Build normalized image requests for ``text2image`` and ``image2image``
  modes, including resolution, ratio, dimensions, references, and batch
  count.
* Validate against the snapshot's models / resolutions / ratios / count
  limits / reference roles.
* Bind every submission to an ``ApprovalReceipt`` whose
  ``request_fingerprint`` matches the canonical SHA-256 of the request
  payload.
* Invoke the argv-only :class:`scripts.dreamina_adapter.DreaminaAdapter`
  exactly once per batch, preserving the per-item results returned by
  the CLI.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.dreamina_adapter import DreaminaAdapter, DreaminaResult


class ImageServiceError(Exception):
    """Base class for image workflow errors."""


class UnsupportedCapabilityError(ImageServiceError):
    """A requested model / resolution / ratio / mode is not in the snapshot."""


class BatchCountOutOfRangeError(ImageServiceError):
    """Batch count is outside [1, 10] or beyond the model's max_count."""


class InvalidReferenceError(ImageServiceError):
    """Reference list violates mode-specific scope, role, or size rules."""


class MissingApprovalError(ImageServiceError):
    """A submission was attempted without an approval receipt."""


class ApprovalMismatchError(ImageServiceError):
    """The approval receipt's request_fingerprint does not match the request."""


GLOBAL_MIN_COUNT = 1
GLOBAL_MAX_COUNT = 10
REFERENCE_LIMIT = 8
REFERENCE_ROLES = {"style", "subject", "frame", "audio", "reference"}
SUBJECT_REQUIRED_MODES = {"image2image"}


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_request_fingerprint(payload: Mapping[str, Any]) -> str:
    """SHA-256 hex digest over canonical JSON serialization."""
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class _ModelSpec:
    name: str
    modes: frozenset[str]
    resolutions: frozenset[str]
    ratios: frozenset[str]
    max_count: int
    max_references: int

    @classmethod
    def from_snapshot(cls, entry: Mapping[str, Any]) -> "_ModelSpec":
        return cls(
            name=str(entry["name"]),
            modes=frozenset(entry.get("modes", [])),
            resolutions=frozenset(entry.get("resolutions", [])),
            ratios=frozenset(entry.get("ratios", [])),
            max_count=int(entry.get("max_count", 1)),
            max_references=int(entry.get("max_references", REFERENCE_LIMIT)),
        )


class ImageService:
    """Build and submit Dreamina image requests against a capability snapshot."""

    def __init__(self, snapshot: Mapping[str, Any], ledger_dir: Path) -> None:
        if "modes" not in snapshot:
            raise UnsupportedCapabilityError("snapshot missing modes")
        self._snapshot = snapshot
        self._modes = frozenset(snapshot.get("modes", []))
        self._ratios = frozenset(snapshot.get("ratios", []))
        self._image_resolutions = frozenset(snapshot.get("resolutions", {}).get("image", []))
        self._models = {
            entry["name"]: _ModelSpec.from_snapshot(entry)
            for entry in snapshot.get("models", [])
            if isinstance(entry, Mapping) and "name" in entry
        }
        self._ledger_dir = Path(ledger_dir)
        self._ledger_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Request construction
    # ------------------------------------------------------------------
    def build_request(
        self,
        *,
        mode: str,
        prompt: str,
        model: str,
        resolution_type: str | None = None,
        count: int = 1,
        ratio: str | None = None,
        width: int | None = None,
        height: int | None = None,
        references: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if mode not in self._modes:
            raise UnsupportedCapabilityError(f"mode not in snapshot: {mode}")
        spec = self._models.get(model)
        if spec is None:
            raise UnsupportedCapabilityError(f"unknown model: {model}")
        if mode not in spec.modes:
            raise UnsupportedCapabilityError(f"model {model} does not support mode {mode}")
        if not resolution_type:
            raise UnsupportedCapabilityError("resolution_type is required for image requests")
        advertised_resolutions = spec.resolutions or self._image_resolutions
        if resolution_type not in advertised_resolutions:
            raise UnsupportedCapabilityError(f"resolution_type not advertised: {resolution_type}")
        if not (GLOBAL_MIN_COUNT <= count <= GLOBAL_MAX_COUNT):
            raise BatchCountOutOfRangeError(
                f"count must be between {GLOBAL_MIN_COUNT} and {GLOBAL_MAX_COUNT}, got {count}"
            )
        if count > spec.max_count:
            raise BatchCountOutOfRangeError(
                f"model {model} supports at most {spec.max_count} images per batch"
            )
        advertised_ratios = spec.ratios or self._ratios
        if ratio is not None and ratio not in advertised_ratios:
            raise UnsupportedCapabilityError(f"ratio not advertised: {ratio}")
        self._validate_dimensions(width=width, height=height, ratio=ratio)
        normalized_refs = self._validate_references(
            mode=mode, references=references or [], max_references=spec.max_references
        )

        request: dict[str, Any] = {
            "mode": mode,
            "prompt": prompt,
            "model": model,
            "count": count,
            "resolution_type": resolution_type,
        }
        if ratio is not None:
            request["ratio"] = ratio
        if width is not None and height is not None:
            request["width"] = width
            request["height"] = height
        if normalized_refs:
            request["references"] = normalized_refs
        return request

    @staticmethod
    def _validate_dimensions(*, width: int | None, height: int | None, ratio: str | None) -> None:
        if (width is None) != (height is None):
            raise UnsupportedCapabilityError("width and height must be provided together")
        if width is not None and height is not None and ratio is not None:
            raise UnsupportedCapabilityError("width/height are mutually exclusive with ratio")

    @staticmethod
    def _validate_references(*, mode: str, references: list[Mapping[str, Any]], max_references: int) -> list[dict[str, Any]]:
        if not references:
            if mode in SUBJECT_REQUIRED_MODES:
                raise InvalidReferenceError(f"mode {mode} requires at least one subject reference")
            return []
        if len(references) > max_references:
            raise InvalidReferenceError(f"too many references (max {max_references})")
        normalized: list[dict[str, Any]] = []
        for ref in references:
            if not isinstance(ref, Mapping):
                raise InvalidReferenceError("reference must be an object")
            role = str(ref.get("role", "")).strip().lower()
            if role not in REFERENCE_ROLES:
                raise InvalidReferenceError(f"unknown reference role: {role}")
            path = ref.get("path")
            if not path:
                raise InvalidReferenceError("reference.path is required")
            entry: dict[str, Any] = {"path": str(path), "role": role}
            mime = ref.get("mime_type")
            size = ref.get("size_bytes")
            if mime is not None:
                entry["mime_type"] = str(mime)
            if size is not None:
                entry["size_bytes"] = int(size)
            normalized.append(entry)
        if mode in SUBJECT_REQUIRED_MODES and not any(r["role"] == "subject" for r in normalized):
            raise InvalidReferenceError(f"mode {mode} requires at least one subject reference")
        if mode == "text2image":
            raise InvalidReferenceError("text2image does not accept references")
        return normalized

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------
    def submit(
        self,
        request: Mapping[str, Any],
        *,
        adapter: DreaminaAdapter | Any,
        approval: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if approval is None:
            raise MissingApprovalError("approval receipt is required before submission")
        expected_fingerprint = build_request_fingerprint(dict(request))
        if approval.get("request_fingerprint") != expected_fingerprint:
            raise ApprovalMismatchError(
                f"approval request_fingerprint mismatch: expected {expected_fingerprint}"
            )
        argv = self._request_to_argv(request)
        result: DreaminaResult = adapter.run(argv)
        payload = result.payload or {}
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if items is None and isinstance(payload, Mapping):
            # Synthesize a single-item result when the CLI returns no items list.
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
            "--generate_num", str(request["count"]),
            "--resolution_type", str(request["resolution_type"]),
        ]
        if "ratio" in request:
            argv.extend(["--ratio", str(request["ratio"])])
        if "width" in request and "height" in request:
            argv.extend(["--width", str(request["width"]), "--height", str(request["height"])])
        for ref in request.get("references", []) or []:
            argv.extend(["--images", str(ref["path"])])
        return argv


__all__ = [
    "ApprovalMismatchError",
    "BatchCountOutOfRangeError",
    "ImageService",
    "InvalidReferenceError",
    "MissingApprovalError",
    "UnsupportedCapabilityError",
    "build_request_fingerprint",
]
