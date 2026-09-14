"""Canonical, fail-closed evaluation of generated video shots."""
from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from scripts.json_contracts import canonical_fingerprint, parse_rfc3339, validate_contract


class EvaluationContractError(ValueError):
    """An evaluation input violates its closed two-source contract."""


MEASURED_GATES = frozenset({"artifact_integrity", "dimensions", "codec", "duration", "aspect_ratio",
                            "frame_readability", "start_anchor", "end_anchor"})
SEMANTIC_GATES = frozenset({"intent", "composition", "identity_continuity", "camera_behavior",
                            "rhythm_function", "temporal_defects", "source_copying", "subtitle_safe_area"})
BINDING_FIELDS = frozenset({"project_id", "batch_version", "shot_id", "attempt", "artifact_sha256",
                            "design_version", "design_fingerprint", "quote_fingerprint", "allowance_id"})
REPAIR_BY_GATE = {"identity_continuity": "identity_consistency", "camera_behavior": "camera_match",
                  "composition": "camera_match", "rhythm_function": "camera_match",
                  "dimensions": "camera_match", "aspect_ratio": "camera_match",
                  "duration": "temporal_stability", "temporal_defects": "temporal_stability",
                  "source_copying": "remove_text", "subtitle_safe_area": "remove_text"}
_DIGEST = re.compile(r"[a-f0-9]{64}")


def _binding(source: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(source, Mapping) or set(source) != BINDING_FIELDS:
        raise EvaluationContractError("evaluation binding must be complete and closed")
    result = copy.deepcopy(dict(source))
    if isinstance(result["attempt"], bool) or not isinstance(result["attempt"], int) or not 1 <= result["attempt"] <= 3:
        raise EvaluationContractError("evaluation attempt is invalid")
    for name in ("artifact_sha256", "design_fingerprint", "quote_fingerprint"):
        if not isinstance(result[name], str) or _DIGEST.fullmatch(result[name]) is None:
            raise EvaluationContractError(f"evaluation {name} is invalid")
    return result


def _validate_gates(payload: Mapping[str, Any], expected: frozenset[str]) -> dict[str, dict[str, str]]:
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise EvaluationContractError("every domain gate is required exactly once")
    result: dict[str, dict[str, str]] = {}
    for name, gate in payload.items():
        if not isinstance(gate, Mapping) or set(gate) != {"status", "evidence"} \
                or gate.get("status") not in {"passed", "failed", "skipped"} \
                or not isinstance(gate.get("evidence"), str) or not gate["evidence"]:
            raise EvaluationContractError(f"evaluation gate {name} is invalid")
        result[str(name)] = copy.deepcopy(dict(gate))
    return result


class VideoEvaluationService:
    """Keep trusted measurements separate from model semantic judgments."""

    def __init__(self, *, media_adapter: Any) -> None:
        if media_adapter is None or not callable(getattr(media_adapter, "probe_json", None)):
            raise TypeError("a trusted media adapter is required")
        self._media_adapter = media_adapter

    def measure_clip(self, artifact: Mapping[str, Any], design_shot: Mapping[str, Any], *,
                     binding: Mapping[str, Any]) -> dict[str, Any]:
        trusted_binding = _binding(binding)
        artifact_conflict = any(name in artifact and artifact.get(name) != trusted_binding[name] for name in BINDING_FIELDS)
        design_conflict = any(name in design_shot and design_shot.get(name) != trusted_binding[name] for name in BINDING_FIELDS)
        try:
            digest = hashlib.sha256(Path(str(artifact["path"])).read_bytes()).hexdigest()
        except (KeyError, OSError):
            digest = None
        try:
            raw_probe = self._media_adapter.probe_json(Path(str(artifact["path"])))
        except Exception:
            raw_probe = {}
        streams = raw_probe.get("streams", []) if isinstance(raw_probe, Mapping) else []
        stream = next((item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "video"), {})
        format_data = raw_probe.get("format", {}) if isinstance(raw_probe, Mapping) and isinstance(raw_probe.get("format"), Mapping) else {}
        width, height, codec = stream.get("width"), stream.get("height"), stream.get("codec_name")
        try: duration = float(format_data["duration"])
        except (KeyError, TypeError, ValueError): duration = None
        readable = True if stream and isinstance(duration, float) and duration > 0 else None

        def gate(passed: bool | None, evidence: str) -> dict[str, str]:
            return {"status": "skipped" if passed is None else "passed" if passed else "failed",
                    "evidence": evidence}

        expected_width, expected_height = design_shot.get("width"), design_shot.get("height")
        ratio = None if not isinstance(width, int) or not isinstance(height, int) or height == 0 else width / height
        expected_ratio = None if not isinstance(expected_width, int) or not isinstance(expected_height, int) or expected_height == 0 else expected_width / expected_height
        expected_duration = design_shot.get("duration_seconds")
        gates = {
            "artifact_integrity": gate(None if artifact_conflict or digest is None else digest == trusted_binding["artifact_sha256"] == artifact.get("sha256"), "trusted artifact identity conflict" if artifact_conflict else "trusted digest verification"),
            "dimensions": gate(None if design_conflict or None in (width, height, expected_width, expected_height) else (width, height) == (expected_width, expected_height), "trusted design identity conflict" if design_conflict else "trusted probe dimensions"),
            "codec": gate(None if design_conflict or codec is None or design_shot.get("codec") is None else codec == design_shot["codec"], "trusted design identity conflict" if design_conflict else "trusted probe codec"),
            "duration": gate(None if not isinstance(duration, (int, float)) or not isinstance(expected_duration, (int, float)) else abs(duration - expected_duration) <= 0.1, "trusted probe duration"),
            "aspect_ratio": gate(None if ratio is None or expected_ratio is None else abs(ratio - expected_ratio) <= 0.001, "trusted probe aspect ratio"),
            "frame_readability": gate(readable, "trusted media probe readability"),
            "start_anchor": gate(readable, "trusted media start anchor"),
            "end_anchor": gate(readable, "trusted media end anchor"),
        }
        evidence = {"width": width, "height": height, "codec": codec, "duration_seconds": duration,
                    "readable": readable, "start_anchor": readable, "end_anchor": readable}
        return {"binding": trusted_binding, "gates": gates, "evidence": evidence}

    def validate_semantic_evaluation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {"binding", "gates"}:
            raise EvaluationContractError("semantic evaluation must be closed")
        return {"binding": _binding(payload["binding"]), "gates": _validate_gates(payload["gates"], SEMANTIC_GATES)}

    @staticmethod
    def _decision(binding: Mapping[str, Any], measured: Mapping[str, Any], semantic: Mapping[str, Any],
                  allowance: Mapping[str, Any], quote: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
        gates = {**measured, **semantic}
        unavailable = [name for name, gate in gates.items() if gate["status"] == "skipped"]
        failed = [name for name, gate in gates.items() if gate["status"] == "failed"]
        failed_gates = failed + unavailable
        quote_binding = (quote.get("project_id"), quote.get("quote_version"), quote.get("design_version"),
                         quote.get("design_fingerprint"), quote.get("quote_fingerprint"))
        allowance_binding = (allowance.get("project_id"), allowance.get("quote_version"), allowance.get("design_version"),
                             allowance.get("design_fingerprint"), allowance.get("quote_fingerprint"))
        expected_binding = (binding["project_id"], binding["batch_version"], binding["design_version"],
                            binding["design_fingerprint"], binding["quote_fingerprint"])
        if quote_binding != expected_binding or allowance_binding != expected_binding \
                or allowance.get("allowance_id") != binding["allowance_id"]:
            return {"action": "manual_review"}, failed_gates
        if unavailable:
            return {"action": "manual_review"}, failed_gates
        if not failed:
            return {"action": "accepted"}, []
        if "artifact_integrity" in failed:
            return {"action": "rejected"}, failed_gates
        directives = {REPAIR_BY_GATE.get(name) for name in failed}
        if None in directives or len(directives) != 1 or allowance.get("state") != "active":
            return {"action": "manual_review"}, failed_gates
        directive = next(iter(directives)); next_attempt = binding["attempt"] + 1
        item = next((entry for entry in quote.get("items", []) if entry.get("shot_id") == binding["shot_id"]), None)
        planned = next((entry for entry in item.get("attempts", []) if entry.get("attempt_number") == next_attempt), None) if isinstance(item, Mapping) else None
        if not isinstance(planned, Mapping) or planned.get("repair_directive") != directive:
            return {"action": "manual_review"}, failed_gates
        fingerprint = planned.get("request_fingerprint")
        request_exists = any(request.get("shot_id") == binding["shot_id"] and request.get("attempt") == next_attempt
                             and request.get("request_fingerprint") == fingerprint for request in allowance.get("requests", []))
        consumed = any(reservation.get("shot_id") == binding["shot_id"] and reservation.get("attempt") == next_attempt
                       for reservation in allowance.get("reservations", []))
        if not request_exists or consumed or not isinstance(fingerprint, str) or _DIGEST.fullmatch(fingerprint) is None:
            return {"action": "manual_review"}, failed_gates
        return {"action": "retry", "repair_directive": directive, "request_fingerprint": fingerprint}, failed_gates

    def evaluate(self, artifact: Mapping[str, Any], design_shot: Mapping[str, Any], semantic_payload: Mapping[str, Any], *,
                 binding: Mapping[str, Any], allowance: Mapping[str, Any], quote: Mapping[str, Any], evaluation_id: str,
                 evaluator: Mapping[str, Any]) -> dict[str, Any]:
        trusted_binding = _binding(binding)
        measured = self.measure_clip(artifact, design_shot, binding=trusted_binding)
        try:
            semantic = self.validate_semantic_evaluation(semantic_payload)
            if semantic["binding"] != trusted_binding:
                raise EvaluationContractError("semantic binding conflict")
        except EvaluationContractError as exc:
            reason = "semantic payload unavailable" if "binding" not in str(exc) else "semantic binding conflict"
            semantic = {"binding": trusted_binding, "gates": {
                name: {"status": "skipped", "evidence": reason} for name in SEMANTIC_GATES}}
        if not isinstance(evaluator, Mapping) or set(evaluator) != {"provider", "model", "evaluated_at"} \
                or not all(isinstance(evaluator.get(name), str) and evaluator[name] for name in ("provider", "model", "evaluated_at")):
            raise EvaluationContractError("evaluator provenance is incomplete")
        if evaluator["provider"] != "codex":
            raise EvaluationContractError("semantic provider must be codex")
        try: parse_rfc3339(evaluator["evaluated_at"], label="evaluated_at")
        except ValueError as exc: raise EvaluationContractError("evaluator timestamp is invalid") from exc
        decision, failed = self._decision(measured["binding"], measured["gates"], semantic["gates"], allowance, quote)
        artifact_evidence = {name: copy.deepcopy(artifact.get(name)) for name in
                             ("path", "mime_type", "size_bytes", "sha256", "provenance")}
        artifact_evidence["probe"] = {name: measured["evidence"].get(name) for name in
                                      ("width", "height", "codec", "duration_seconds")}
        artifact_evidence["frames"] = {name: measured["evidence"].get(name) for name in
                                       ("readable", "start_anchor", "end_anchor")}
        receipt = {"schema_version": "1.0", "evaluation_id": evaluation_id,
                   "binding": measured["binding"], "artifact_evidence": artifact_evidence,
                   "measured_gates": measured["gates"], "semantic_gates": semantic["gates"],
                   "failed_gates": failed, "decision": decision,
                   "evaluator": {**copy.deepcopy(dict(evaluator)), "trust_level": "untrusted"}}
        receipt["evaluation_fingerprint"] = canonical_fingerprint(receipt)
        try: validate_contract(receipt, "shot_evaluation.schema.json")
        except ValueError as exc: raise EvaluationContractError("evaluation receipt is invalid") from exc
        return receipt

    def verify_receipt(self, receipt: Mapping[str, Any], *, artifact: Mapping[str, Any],
                       design_shot: Mapping[str, Any], allowance: Mapping[str, Any],
                       quote: Mapping[str, Any]) -> dict[str, Any]:
        """Rebuild a receipt from current trusted media and compare every decision field."""
        rebuilt = self.evaluate(artifact, design_shot,
            {"binding": receipt.get("binding"), "gates": receipt.get("semantic_gates")},
            binding=receipt.get("binding"), allowance=allowance, quote=quote,
            evaluation_id=receipt.get("evaluation_id"), evaluator={name: receipt.get("evaluator", {}).get(name)
                for name in ("provider", "model", "evaluated_at")})
        if rebuilt != dict(receipt):
            raise EvaluationContractError("evaluation receipt does not match trusted recomputation")
        return rebuilt


__all__ = ["EvaluationContractError", "VideoEvaluationService", "MEASURED_GATES", "SEMANTIC_GATES"]
