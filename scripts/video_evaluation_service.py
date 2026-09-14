"""Fail-closed evaluation of generated video shots from two independent sources."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping


class EvaluationContractError(ValueError):
    """An evaluation payload violates the closed two-source contract."""


MEASURED_GATES = frozenset({
    "artifact_integrity", "dimensions", "codec", "duration", "aspect_ratio",
    "frame_readability", "start_anchor", "end_anchor",
})
SEMANTIC_GATES = frozenset({
    "intent", "composition", "identity_continuity", "camera_behavior",
    "rhythm_function", "temporal_defects", "source_copying", "subtitle_safe_area",
})
BINDING_FIELDS = frozenset({
    "project_id", "batch_version", "shot_id", "attempt", "artifact_sha256",
    "design_version", "design_fingerprint", "quote_fingerprint", "allowance_id",
})
REPAIR_BY_GATE = {
    "identity_continuity": "identity_consistency",
    "camera_behavior": "camera_match", "composition": "camera_match",
    "rhythm_function": "camera_match", "dimensions": "camera_match",
    "aspect_ratio": "camera_match", "duration": "temporal_stability",
    "temporal_defects": "temporal_stability", "source_copying": "remove_text",
    "subtitle_safe_area": "remove_text",
}
_DIGEST = re.compile(r"[a-f0-9]{64}")


def _binding(source: Mapping[str, Any]) -> dict[str, Any]:
    result = {name: source.get(name) for name in BINDING_FIELDS}
    if set(result) != BINDING_FIELDS or any(value is None for value in result.values()):
        raise EvaluationContractError("evaluation binding is incomplete")
    if not isinstance(result["attempt"], int) or isinstance(result["attempt"], bool) or result["attempt"] < 1:
        raise EvaluationContractError("evaluation attempt is invalid")
    for name in ("artifact_sha256", "design_fingerprint", "quote_fingerprint"):
        if not isinstance(result[name], str) or _DIGEST.fullmatch(result[name]) is None:
            raise EvaluationContractError(f"evaluation {name} is invalid")
    return result


def _validate_gates(payload: Mapping[str, Any], expected: frozenset[str]) -> dict[str, Any]:
    if set(payload) != {"binding", "gates"} or not isinstance(payload.get("binding"), Mapping) \
            or not isinstance(payload.get("gates"), list):
        raise EvaluationContractError("evaluation payload must be closed")
    binding = _binding(payload["binding"])
    gates: list[dict[str, str]] = []
    for gate in payload["gates"]:
        if not isinstance(gate, Mapping) or set(gate) != {"name", "status", "evidence"}:
            raise EvaluationContractError("evaluation gate must be closed")
        if gate.get("name") not in expected or gate.get("status") not in {"passed", "failed", "unavailable"} \
                or not isinstance(gate.get("evidence"), str) or not gate["evidence"]:
            raise EvaluationContractError("evaluation gate is invalid")
        gates.append(dict(gate))
    if {gate["name"] for gate in gates} != expected or len(gates) != len(expected):
        raise EvaluationContractError("every evaluation gate is required exactly once")
    return {"binding": binding, "gates": gates}


class VideoEvaluationService:
    """Keep trusted measurements separate from model-only semantic judgments."""

    def measure_clip(self, artifact: Mapping[str, Any], design_shot: Mapping[str, Any]) -> dict[str, Any]:
        """Measure the eight deterministic gates using only trusted artifact evidence."""
        artifact_binding = _binding(artifact)
        shot_binding = _binding(design_shot)
        if artifact_binding != shot_binding or artifact.get("sha256") != artifact_binding["artifact_sha256"]:
            raise EvaluationContractError("artifact and design shot bindings conflict")
        probe = artifact.get("probe") if isinstance(artifact.get("probe"), Mapping) else {}
        frames = artifact.get("frames") if isinstance(artifact.get("frames"), Mapping) else {}
        path = artifact.get("path")
        actual_digest = None
        try:
            if isinstance(path, str): actual_digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            actual_digest = None

        def gate(name: str, passed: bool | None, evidence: str) -> dict[str, str]:
            return {"name": name, "status": "unavailable" if passed is None else "passed" if passed else "failed", "evidence": evidence}

        width, height = probe.get("width"), probe.get("height")
        expected_width, expected_height = design_shot.get("width"), design_shot.get("height")
        actual_ratio = None if not isinstance(width, int) or not isinstance(height, int) or height == 0 else width / height
        expected_ratio = None if not isinstance(expected_width, int) or not isinstance(expected_height, int) or expected_height == 0 else expected_width / expected_height
        duration, expected_duration = probe.get("duration_seconds"), design_shot.get("duration_seconds")
        gates = [
            gate("artifact_integrity", None if actual_digest is None else actual_digest == artifact.get("verified_sha256") == artifact.get("sha256"), "trusted digest verification"),
            gate("dimensions", None if None in (width, height, expected_width, expected_height) else (width, height) == (expected_width, expected_height), "trusted probe dimensions"),
            gate("codec", None if probe.get("codec") is None or design_shot.get("codec") is None else probe["codec"] == design_shot["codec"], "trusted probe codec"),
            gate("duration", None if not isinstance(duration, (int, float)) or not isinstance(expected_duration, (int, float)) else abs(duration - expected_duration) <= 0.1, "trusted probe duration"),
            gate("aspect_ratio", None if actual_ratio is None or expected_ratio is None else abs(actual_ratio - expected_ratio) <= 0.001, "trusted probe aspect ratio"),
            gate("frame_readability", frames.get("readable") if isinstance(frames.get("readable"), bool) else None, "trusted decoded frames"),
            gate("start_anchor", frames.get("start_anchor") if isinstance(frames.get("start_anchor"), bool) else None, "trusted start frame"),
            gate("end_anchor", frames.get("end_anchor") if isinstance(frames.get("end_anchor"), bool) else None, "trusted end frame"),
        ]
        return {"binding": artifact_binding, "gates": gates}

    def validate_semantic_evaluation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a model response containing exactly the eight semantic gates."""
        if not isinstance(payload, Mapping):
            raise EvaluationContractError("semantic evaluation must be an object")
        return _validate_gates(payload, SEMANTIC_GATES)

    def decide(self, measured: Mapping[str, Any], semantic: Mapping[str, Any], allowance: Mapping[str, Any]) -> dict[str, Any]:
        """Select an accepted result or one exact remaining prequoted retry."""
        try:
            measured_valid = _validate_gates(measured, MEASURED_GATES)
            semantic_valid = self.validate_semantic_evaluation(semantic)
        except EvaluationContractError:
            return {"action": "manual_review", "failed_gates": []}
        binding = measured_valid["binding"]
        if binding != semantic_valid["binding"]:
            return {"action": "manual_review", "failed_gates": []}
        gates = measured_valid["gates"] + semantic_valid["gates"]
        unavailable = [gate["name"] for gate in gates if gate["status"] == "unavailable"]
        failed = [gate["name"] for gate in gates if gate["status"] == "failed"]
        base = {"binding": binding, "failed_gates": failed + unavailable}
        if unavailable:
            return {"action": "manual_review", **base}
        if not failed:
            return {"action": "accepted", **base}
        directives = {REPAIR_BY_GATE.get(name) for name in failed}
        if None in directives or len(directives) != 1:
            return {"action": "manual_review", **base}
        directive = next(iter(directives))
        allowance_fields = ("project_id", "batch_version", "design_version", "design_fingerprint",
                            "quote_fingerprint", "allowance_id")
        if any(allowance.get(name) != binding[name] for name in allowance_fields):
            return {"action": "manual_review", **base}
        requests = allowance.get("requests") if isinstance(allowance, Mapping) else None
        if not isinstance(requests, list):
            return {"action": "manual_review", **base}
        matches = [request for request in requests if isinstance(request, Mapping)
                   and request.get("shot_id") == binding["shot_id"]
                   and request.get("attempt") == binding["attempt"] + 1
                   and request.get("repair_directive") == directive
                   and request.get("state") == "available"
                   and isinstance(request.get("request_fingerprint"), str)
                   and _DIGEST.fullmatch(request["request_fingerprint"])]
        if len(matches) != 1:
            return {"action": "manual_review", **base}
        return {"action": "retry", "repair_directive": directive,
                "request_fingerprint": matches[0]["request_fingerprint"], **base}


__all__ = ["EvaluationContractError", "VideoEvaluationService", "MEASURED_GATES", "SEMANTIC_GATES"]
