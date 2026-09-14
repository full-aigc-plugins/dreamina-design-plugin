"""Semantic invariants for closed ReelBench evidence and comparison receipts."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping

from scripts.json_contracts import ContractValidationError, canonical_fingerprint, parse_rfc3339, validate_contract

REELBENCH_VALIDATE_GATES = (
    "timeline", "duration", "numbering", "size", "category", "camera", "transition",
    "frame-text", "dedup", "subjects", "category-evidence", "motion", "boundary", "frames", "rhythm",
)
_SYNC_VERIFY_GATES = frozenset({"duration", "dimensions", "codec", "audio_policy", "cut_alignment", "highlight_alignment"})
_EARLY_ACTIONS = frozenset({"seed", "evidence", "render", "plan", "panels", "export"})


def validate_reelbench_evidence(receipt: Mapping[str, Any]) -> None:
    """Validate schema plus fingerprint, timestamp, path, and action-specific gate invariants."""
    validate_contract(receipt, "reelbench_evidence.schema.json")
    _assert_fingerprint(receipt, "evidence_fingerprint")
    parse_rfc3339(receipt["created_at"], label="created_at")
    parse_rfc3339(receipt["committed_at"], label="committed_at")
    for artifact in receipt["artifacts"]: _assert_relative_artifact_path(artifact["path"])
    gates = receipt["gates"]
    names = [gate["name"] for gate in gates]
    if len(names) != len(set(names)): raise ContractValidationError("ReelBench gate names must be unique")
    action = receipt["action"]
    if action == "validate":
        if set(names) != set(REELBENCH_VALIDATE_GATES) or len(names) != len(REELBENCH_VALIDATE_GATES): raise ContractValidationError("validate action requires exactly all 15 upstream gates once")
        if any(gate["status"] == "SKIPPED" and gate["name"] not in {"subjects", "motion", "boundary", "frames"} for gate in gates):
            raise ContractValidationError("only upstream optional validation gates may be SKIPPED")
    elif action == "verify":
        if set(names) != _SYNC_VERIFY_GATES or len(names) != len(_SYNC_VERIFY_GATES): raise ContractValidationError("sync verify action requires exactly six gates")
        if any(gate["status"] == "SKIPPED" for gate in gates): raise ContractValidationError("sync verify gates may not be SKIPPED")
    elif action in _EARLY_ACTIONS and names:
        raise ContractValidationError("this ReelBench action must not claim validation gates")


def validate_reelbench_comparison(receipt: Mapping[str, Any]) -> None:
    """Validate schema plus comparison fingerprint and deterministic overall verdict semantics."""
    validate_contract(receipt, "reelbench_comparison.schema.json")
    _assert_fingerprint(receipt, "comparison_fingerprint")
    parse_rfc3339(receipt["compared_at"], label="compared_at")
    verdicts = {domain["verdict"] for domain in receipt["domains"].values()}
    required = "manual_review" if "manual_review" in verdicts else "matched"
    if receipt["overall"] != required: raise ContractValidationError("comparison overall must match every domain verdict")


def _assert_fingerprint(receipt: Mapping[str, Any], key: str) -> None:
    core = {name: value for name, value in receipt.items() if name != key}
    if receipt[key] != canonical_fingerprint(core): raise ContractValidationError(f"{key} does not match the canonical receipt fingerprint")


def _assert_relative_artifact_path(value: Any) -> None:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value or value.endswith("/"):
        raise ContractValidationError("artifact path must be canonical project-relative")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractValidationError("artifact path must be canonical project-relative")


__all__ = ["REELBENCH_VALIDATE_GATES", "validate_reelbench_comparison", "validate_reelbench_evidence"]
