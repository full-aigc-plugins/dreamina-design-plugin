"""Deterministic, non-submitting planner for immutable Dreamina batch quotes."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from scripts.json_contracts import canonical_fingerprint, validate_contract
from scripts.video_service import (
    DurationOutOfRangeError,
    UnsupportedCapabilityError,
    VideoService,
    build_video_request_fingerprint,
)


class PlanningError(ValueError):
    """A design cannot be converted into a closed, exact request batch."""


class CostBasisError(PlanningError):
    """No auditable per-attempt credit ceiling is available."""


REPAIR_DIRECTIVES = {
    "identity_consistency": "Keep the approved subject identity, wardrobe, and proportions unchanged.",
    "camera_match": "Apply only the approved camera movement and preserve the designed framing.",
    "remove_text": "Remove unintended visible text, logos, and watermarks.",
    "temporal_stability": "Keep geometry and texture temporally stable without flicker or morphing.",
}


def quote_total(items: Sequence[Mapping[str, Any]]) -> int:
    """Return the literal sum of all pre-enumerated attempt ceilings."""
    return sum(
        int(item["credit_ceiling"])
        for item in items
        for _ in item["request_fingerprints"]
    )


def validate_batch_quote(quote: Mapping[str, Any]) -> None:
    """Validate the closed schema and every derived quote integrity field."""
    validate_contract(quote, "video_batch_quote.schema.json")
    items = quote["items"]
    if quote["item_count"] != len(items):
        raise PlanningError("item_count does not match items")
    task_count = sum(len(item["attempts"]) for item in items)
    if quote["task_count"] != task_count:
        raise PlanningError("task_count does not match attempts")
    if quote["total_credit_ceiling"] != quote_total(items):
        raise PlanningError("total_credit_ceiling does not match attempts")
    for item in items:
        fingerprints = [attempt["request_fingerprint"] for attempt in item["attempts"]]
        if item["request_fingerprints"] != fingerprints:
            raise PlanningError("request_fingerprints do not match attempts")
        for attempt in item["attempts"]:
            if attempt["credit_ceiling"] != item["credit_ceiling"]:
                raise PlanningError("attempt credit_ceiling does not match item")
            if attempt["request_fingerprint"] != canonical_fingerprint(attempt["request"]):
                raise PlanningError("request_fingerprint does not match request")
    core = {key: copy.deepcopy(value) for key, value in quote.items() if key != "quote_fingerprint"}
    if quote["quote_fingerprint"] != canonical_fingerprint(core):
        raise PlanningError("quote_fingerprint does not match quote")


class VideoGenerationPlanner:
    """Materialize every base/retry request without approving or submitting it."""

    def __init__(self, reference_policy: Any | None = None) -> None:
        self._reference_policy = reference_policy

    def plan_shot(self, shot: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Choose one mode deterministically and build its exact base request."""
        storyboard = list(shot.get("storyboard", []))
        references = copy.deepcopy(list(shot.get("references", [])))
        if storyboard and references:
            raise PlanningError("storyboard cannot be combined with separate references")
        for reference in references:
            if reference.get("role") == "video":
                reference["role"] = "reference"
        if storyboard:
            mode = "multiframe2video"
            references = copy.deepcopy(storyboard)
        elif any(str(ref.get("role", "")).lower() in {"audio", "reference", "video"} for ref in references):
            mode = "multimodal2video"
        elif len(references) == 2 and all(str(ref.get("role", "")).lower() == "frame" for ref in references):
            mode = "frames2video"
        elif len(references) == 1 and str(references[0].get("role", "")).lower() in {"subject", "style"}:
            mode = "image2video"
        elif not references:
            mode = "text2video"
        else:
            raise PlanningError("references do not map to one deterministic video mode")
        if mode not in snapshot.get("modes", []):
            raise UnsupportedCapabilityError(f"mode not in snapshot: {mode}")
        self._require_advertised_constraints(shot, snapshot, mode)

        service = VideoService(snapshot=snapshot, ledger_dir=None, reference_policy=self._reference_policy)
        kwargs: dict[str, Any] = {
            "mode": mode,
            "prompt": str(shot.get("prompt", "")),
            "model": None if mode == "multiframe2video" else shot.get("model"),
            "video_resolution": shot.get("video_resolution"),
            "ratio": None if mode == "multiframe2video" else shot.get("ratio"),
            "duration_seconds": shot.get("duration_seconds"),
            "references": references,
        }
        if mode == "multiframe2video":
            kwargs["transitions"] = copy.deepcopy(list(shot.get("transitions", [])))
        try:
            request = service.build_request(**kwargs)
        except DurationOutOfRangeError as exc:
            raise UnsupportedCapabilityError(str(exc)) from exc
        return {"shot_id": str(shot.get("id", "")), "mode": mode, "request": request}

    def plan(
        self,
        design: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        cost_basis: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a closed quote whose attempts and costs are all known in advance."""
        ceiling, normalized_cost = self._resolve_cost(snapshot, cost_basis)
        shots = design.get("shots")
        if shots is None and isinstance(design.get("payload"), Mapping):
            shots = design["payload"].get("shots")
        if not isinstance(shots, list) or not shots:
            raise PlanningError("design must contain at least one shot")

        items: list[dict[str, Any]] = []
        for shot in shots:
            if "retry_prompt" in shot:
                raise PlanningError("free-form retry prompt is forbidden")
            max_attempts = int(shot.get("max_attempts", 1))
            if not 1 <= max_attempts <= 3:
                raise PlanningError("max_attempts must be between 1 and 3")
            repairs = list(shot.get("repair_directives", []))
            if len(repairs) != max_attempts - 1:
                raise PlanningError("one closed repair directive is required for each retry")
            if any(key not in REPAIR_DIRECTIVES for key in repairs):
                raise PlanningError("retry must use a closed repair directive")

            planned = self.plan_shot(shot, snapshot)
            attempts = [self._attempt(1, None, planned["request"], ceiling)]
            for index, repair_key in enumerate(repairs, start=2):
                retry = copy.deepcopy(planned["request"])
                retry["prompt"] = f'{retry["prompt"]}\n\nRepair directive: {REPAIR_DIRECTIVES[repair_key]}'
                attempts.append(self._attempt(index, repair_key, retry, ceiling))
            items.append({
                "shot_id": planned["shot_id"], "mode": planned["mode"],
                "credit_ceiling": ceiling,
                "request_fingerprints": [entry["request_fingerprint"] for entry in attempts],
                "attempts": attempts,
            })

        quote: dict[str, Any] = {
            "schema_version": "1.0", "quote_version": "v001",
            "project_id": design.get("project_id"), "design_version": design.get("version"),
            "design_fingerprint": design.get("design_fingerprint"),
            "rights_receipt_id": design.get("rights_receipt_id"),
            "source_sha256": design.get("source_sha256"),
            "analysis_version": design.get("analysis_version"),
            "machine_fingerprint": design.get("machine_fingerprint"),
            "creative_mode": design.get("creative_mode"),
            "audio_policy": design.get("audio_policy"),
            "output_destination": design.get("output_destination"),
            "output_profile": copy.deepcopy(design.get("output_profile")),
            "capability_snapshot_fingerprint": canonical_fingerprint(snapshot),
            "cost_basis": normalized_cost, "items": items,
            "item_count": len(items),
            "task_count": sum(len(item["attempts"]) for item in items),
            "total_credit_ceiling": quote_total(items),
            "quoted_at": normalized_cost["recorded_at"],
        }
        quote["quote_fingerprint"] = canonical_fingerprint(quote)
        validate_batch_quote(quote)
        return copy.deepcopy(quote)

    @staticmethod
    def validate_quote(quote: Mapping[str, Any]) -> None:
        """Validate a previously materialized quote before later approval stages."""
        validate_batch_quote(quote)

    @staticmethod
    def _require_advertised_constraints(
        shot: Mapping[str, Any], snapshot: Mapping[str, Any], mode: str
    ) -> None:
        """Reject planner inputs whose validation would rely on service defaults."""
        if mode == "multiframe2video":
            bounds = snapshot.get("durations")
            if not isinstance(bounds, Mapping) or not all(
                key in bounds for key in ("min_seconds", "max_seconds")
            ):
                raise UnsupportedCapabilityError(
                    "snapshot missing advertised duration bounds for multiframe2video"
                )
            duration = shot.get("duration_seconds")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, int)
                or not int(bounds["min_seconds"]) <= duration <= int(bounds["max_seconds"])
            ):
                raise UnsupportedCapabilityError(
                    f"duration_seconds not advertised for multiframe2video: {duration}"
                )
            return

        model = shot.get("model")
        entry = next(
            (
                candidate
                for candidate in snapshot.get("models", [])
                if isinstance(candidate, Mapping) and candidate.get("name") == model
            ),
            None,
        )
        if not isinstance(entry, Mapping):
            raise UnsupportedCapabilityError(f"unknown model: {model}")
        if not all(key in entry for key in ("duration_min_seconds", "duration_max_seconds")):
            raise UnsupportedCapabilityError(f"snapshot missing duration bounds for model {model}")
        if not isinstance(entry.get("resolutions"), list) or not entry["resolutions"]:
            raise UnsupportedCapabilityError(f"snapshot missing resolutions for model {model}")
        if not isinstance(entry.get("ratios"), list) or not entry["ratios"]:
            raise UnsupportedCapabilityError(f"snapshot missing ratios for model {model}")
        references = shot.get("references", [])
        if references and "max_references" not in entry:
            raise UnsupportedCapabilityError(f"snapshot missing reference limit for model {model}")
        if any(str(reference.get("role", "")).lower() == "audio" for reference in references):
            if "audio_reference_max_seconds" not in entry:
                raise UnsupportedCapabilityError(
                    f"snapshot missing audio reference duration limit for model {model}"
                )

    @staticmethod
    def _attempt(number: int, repair_key: str | None, request: Mapping[str, Any], ceiling: int) -> dict[str, Any]:
        exact = copy.deepcopy(dict(request))
        return {
            "attempt_number": number, "repair_directive": repair_key,
            "request": exact, "request_fingerprint": build_video_request_fingerprint(exact),
            "credit_ceiling": ceiling,
        }

    @staticmethod
    def _resolve_cost(snapshot: Mapping[str, Any], cost_basis: Mapping[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if cost_basis is not None:
            if cost_basis.get("kind") != "operator_ceiling" or not cost_basis.get("source") or not cost_basis.get("recorded_at"):
                raise CostBasisError("explicit operator ceiling requires source and recorded_at")
            ceiling = cost_basis.get("credit_ceiling")
            if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 1:
                raise CostBasisError("explicit operator ceiling must be a positive integer")
            return ceiling, copy.deepcopy(dict(cost_basis))
        pricing = snapshot.get("pricing")
        if isinstance(pricing, Mapping):
            ceiling = pricing.get("credit_ceiling_per_attempt")
            if isinstance(ceiling, int) and not isinstance(ceiling, bool) and ceiling > 0 and pricing.get("source") and pricing.get("captured_at"):
                return ceiling, {
                    "kind": "live_snapshot", "credit_ceiling": ceiling, "currency": "credits",
                    "source": pricing["source"], "recorded_at": pricing["captured_at"],
                }
        raise CostBasisError("live machine-readable price unavailable; explicit operator ceiling required")
