"""Deterministic, non-submitting planner for immutable Dreamina batch quotes."""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

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

_RFC3339 = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


def _require_rfc3339(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _RFC3339.fullmatch(value) is None:
        raise CostBasisError(f"{label} must be a strict RFC3339 timestamp with timezone")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise CostBasisError(f"{label} must be a valid RFC3339 timestamp") from exc
    return value


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
    _require_rfc3339(quote["quoted_at"], label="quoted_at")
    items = quote["items"]
    if quote["item_count"] != len(items):
        raise PlanningError("item_count does not match items")
    task_count = sum(len(item["attempts"]) for item in items)
    if quote["task_count"] != task_count:
        raise PlanningError("task_count does not match attempts")
    if quote["reserved_retry_count"] != task_count - len(items):
        raise PlanningError("reserved_retry_count does not match attempts")
    target_duration = sum(int(item["attempts"][0]["request"]["duration_seconds"]) for item in items)
    if quote["target_total_duration_seconds"] != target_duration:
        raise PlanningError("target_total_duration_seconds does not match base requests")
    if quote["total_credit_ceiling"] != quote_total(items):
        raise PlanningError("total_credit_ceiling does not match attempts")
    for item in items:
        fingerprints = [attempt["request_fingerprint"] for attempt in item["attempts"]]
        if item["request_fingerprints"] != fingerprints:
            raise PlanningError("request_fingerprints do not match attempts")
        for attempt in item["attempts"]:
            if attempt["credit_ceiling"] != item["credit_ceiling"]:
                raise PlanningError("attempt credit_ceiling does not match item")
            if attempt["request_fingerprint"] != build_video_request_fingerprint(attempt["request"]):
                raise PlanningError("request_fingerprint does not match request")
    core = {key: copy.deepcopy(value) for key, value in quote.items() if key != "quote_fingerprint"}
    if quote["quote_fingerprint"] != canonical_fingerprint(core):
        raise PlanningError("quote_fingerprint does not match quote")


class VideoGenerationPlanner:
    """Materialize every base/retry request without approving or submitting it."""

    def __init__(self, reference_policy: Any | None = None, *, project_store: Any | None = None, capability_provider_factory: Callable[[], Any] | None = None, now: Any | None = None, snapshot_max_age_seconds: int = 86400) -> None:
        self._reference_policy = reference_policy
        self._project_store = project_store
        self._capability_provider_factory = capability_provider_factory
        self._now = now or (lambda: datetime.now(timezone.utc))
        if isinstance(snapshot_max_age_seconds, bool) or not isinstance(snapshot_max_age_seconds, int) or snapshot_max_age_seconds < 1:
            raise ValueError("snapshot_max_age_seconds must be a positive integer")
        self._snapshot_max_age = timedelta(seconds=snapshot_max_age_seconds)

    def _plan_shot(self, shot: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
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

        policy = self._reference_policy
        if policy is not None and hasattr(policy, "validate_for_quote"):
            stable_validator = policy.validate_for_quote

            class _StablePolicy:
                def validate(self, reference: Mapping[str, Any]) -> dict[str, Any]:
                    return stable_validator(reference)

            policy = _StablePolicy()
        service = VideoService(snapshot=snapshot, ledger_dir=None, reference_policy=policy)
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

    def _plan_materialized(
        self,
        design: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        cost_basis: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a closed quote whose attempts and costs are all known in advance."""
        self._validate_snapshot(snapshot)
        ceiling, normalized_cost = self._resolve_cost(snapshot, cost_basis)
        shots = design.get("shots")
        if shots is None and isinstance(design.get("payload"), Mapping):
            shots = design["payload"].get("shots")
        if not isinstance(shots, list) or not shots:
            raise PlanningError("design must contain at least one shot")
        shot_ids = [shot.get("id") for shot in shots if isinstance(shot, Mapping)]
        if len(shot_ids) != len(shots) or len(set(shot_ids)) != len(shot_ids):
            raise PlanningError("duplicate shot id or invalid shot entry")
        output_profile = design.get("output_profile")
        if not isinstance(output_profile, Mapping) or output_profile.get("codec") != "h264":
            raise PlanningError("output profile codec must be h264")

        items: list[dict[str, Any]] = []
        for shot in shots:
            if "retry_prompt" in shot:
                raise PlanningError("free-form retry prompt is forbidden")
            raw_max_attempts = shot.get("max_attempts", 1)
            if isinstance(raw_max_attempts, bool) or not isinstance(raw_max_attempts, int):
                raise PlanningError("max_attempts must be an integer between 1 and 3")
            max_attempts = raw_max_attempts
            if not 1 <= max_attempts <= 3:
                raise PlanningError("max_attempts must be between 1 and 3")
            repairs = list(shot.get("repair_directives", []))
            if len(repairs) != max_attempts - 1:
                raise PlanningError("one closed repair directive is required for each retry")
            if any(key not in REPAIR_DIRECTIVES for key in repairs):
                raise PlanningError("retry must use a closed repair directive")

            planned = self._plan_shot(shot, snapshot)
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
            "reserved_retry_count": sum(len(item["attempts"]) - 1 for item in items),
            "target_total_duration_seconds": sum(
                int(item["attempts"][0]["request"]["duration_seconds"]) for item in items
            ),
            "total_credit_ceiling": quote_total(items),
            "quoted_at": normalized_cost["recorded_at"],
        }
        quote["quote_fingerprint"] = canonical_fingerprint(quote)
        validate_batch_quote(quote)
        return copy.deepcopy(quote)

    def plan(
        self, project_id: str, design_version: str, cost_basis: Mapping[str, Any] | None, *, generation: Mapping[str, Mapping[str, Any]],
        output_destination: str, output_profile: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Plan from one safely read committed design and its current evidence."""
        if self._project_store is None:
            raise PlanningError("persisted planning requires a VideoProjectStore")
        if self._capability_provider_factory is None:
            raise PlanningError("production planning requires a trusted capability provider")
        provider = self._capability_provider_factory()
        from scripts.trusted_capability_provider import TrustedCapabilityProvider
        if type(provider) is not TrustedCapabilityProvider:
            raise PlanningError("production planning requires TrustedCapabilityProvider")
        try:
            evidence = json.loads(json.dumps(provider.capture(), ensure_ascii=False, allow_nan=False))
        except (AttributeError, TypeError, ValueError) as exc:
            raise PlanningError("trusted capability evidence is not canonical JSON") from exc
        if not isinstance(evidence, Mapping) or set(evidence) != {"snapshot", "identity_receipt"}:
            raise PlanningError("trusted capability evidence must be complete and closed")
        snapshot = evidence["snapshot"]
        receipt = evidence["identity_receipt"]
        if not isinstance(snapshot, Mapping) or not isinstance(receipt, Mapping):
            raise PlanningError("trusted capability evidence is invalid")
        self._validate_snapshot(snapshot)
        receipt_core = {
            "cli_version": snapshot.get("cli_version"),
            "cli_commit": snapshot.get("cli_commit"),
            "snapshot_fingerprint": canonical_fingerprint(snapshot),
            "captured_at": snapshot.get("captured_at"),
        }
        if any(receipt.get(key) != value for key, value in receipt_core.items()) or set(receipt) != {
            "cli_path", "cli_sha256", "device", "inode", "size_bytes", "mode", "owner_uid",
            "cli_version", "cli_commit", "captured_at", "snapshot_fingerprint",
        }:
            raise PlanningError("capability identity receipt does not bind the snapshot")
        if (
            not isinstance(receipt.get("cli_path"), str)
            or not receipt["cli_path"].startswith("/")
            or re.fullmatch(r"[a-f0-9]{64}", str(receipt.get("cli_sha256"))) is None
            or any(isinstance(receipt.get(key), bool) or not isinstance(receipt.get(key), int) for key in ("device", "inode", "size_bytes", "mode", "owner_uid"))
            or receipt["device"] < 0 or receipt["inode"] < 1 or receipt["size_bytes"] < 1
            or receipt["owner_uid"] not in {0, os.getuid()}
            or receipt["mode"] & 0o022
        ):
            raise PlanningError("capability identity receipt has unsafe CLI identity fields")
        from scripts.video_redesign_service import VideoRedesignService
        from scripts.video_rights_service import VideoRightsService

        design = self._project_store.read_version(project_id, "redesign", design_version, "video_redesign.schema.json")
        project = self._project_store.get(project_id)
        if design["project_id"] != project_id or design["creative_mode"] != project["creative_mode"]:
            raise PlanningError("persisted design mode or project binding mismatch")
        VideoRedesignService(self._project_store)._validate_candidate_against_current_evidence(design)
        payload = design["payload"]
        if design["creative_mode"] == "authorized_replication":
            receipt_id = design.get("rights_receipt_id")
            if not isinstance(receipt_id, str):
                raise PlanningError("authorized replication requires an exact rights receipt")
            receipt = self._project_store.find_version_by_field(project_id, "rights_receipt", field="receipt_id", value=receipt_id, schema_name="video_rights_receipt.schema.json")
            VideoRightsService(self._project_store, native_confirmer=None).assert_scope(
                receipt, required=set(payload["preserve"]), binding={
                    "project_id": project_id, "source_sha256": design["source_sha256"],
                    "creative_mode": design["creative_mode"], "design_fingerprint": design["design_fingerprint"],
                    "required_media": payload["required_media"], "purpose": payload["purpose"],
                    "audience": payload["audience"], "territory": payload["territory"],
                })
        elif design.get("rights_receipt_id") is not None:
            raise PlanningError("original redesign must not carry a replication receipt")
        shots = []
        for source_shot in payload["shots"]:
            options = generation.get(source_shot["id"])
            if not isinstance(options, Mapping):
                raise PlanningError(f"generation options missing for {source_shot['id']}")
            shots.append({**copy.deepcopy(dict(options)), "id": source_shot["id"], "prompt": source_shot["prompt"]})
        materialized = {
            **{key: copy.deepcopy(design[key]) for key in ("project_id", "version", "design_fingerprint", "rights_receipt_id", "source_sha256", "analysis_version", "machine_fingerprint", "creative_mode")},
            "audio_policy": project["audio_policy"], "output_destination": output_destination,
            "output_profile": copy.deepcopy(dict(output_profile)), "shots": shots,
        }
        return self._plan_materialized(materialized, snapshot, cost_basis)

    def _validate_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        try:
            validate_contract(snapshot, "capability_snapshot.schema.json")
            captured = datetime.fromisoformat(_require_rfc3339(snapshot["captured_at"], label="captured_at").replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception as exc:
            raise UnsupportedCapabilityError("capability snapshot is not valid machine-readable evidence") from exc
        current = self._now()
        if isinstance(current, str):
            current = datetime.fromisoformat(_require_rfc3339(current, label="now").replace("Z", "+00:00"))
        current = current.astimezone(timezone.utc)
        if captured > current + timedelta(minutes=5) or current - captured > self._snapshot_max_age:
            raise UnsupportedCapabilityError("capability snapshot is stale or from the future")

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
            mode_limits = snapshot.get("mode_limits")
            bounds = mode_limits.get(mode) if isinstance(mode_limits, Mapping) else None
            if not isinstance(bounds, Mapping) or not all(
                key in bounds
                for key in ("min_references", "max_references", "request_duration_min_seconds", "request_duration_max_seconds", "transition_duration_min_seconds", "transition_duration_max_seconds")
            ):
                raise UnsupportedCapabilityError(
                    "snapshot missing advertised duration bounds or reference bounds for multiframe2video"
                )
            duration = shot.get("duration_seconds")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, int)
                or not int(bounds["request_duration_min_seconds"]) <= duration <= int(bounds["request_duration_max_seconds"])
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

    def _resolve_cost(self, snapshot: Mapping[str, Any], cost_basis: Mapping[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if cost_basis is not None:
            if cost_basis.get("kind") != "operator_ceiling" or not cost_basis.get("source") or not cost_basis.get("recorded_at"):
                raise CostBasisError("explicit operator ceiling requires source and recorded_at")
            ceiling = cost_basis.get("credit_ceiling")
            if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 1:
                raise CostBasisError("explicit operator ceiling must be a positive integer")
            normalized = copy.deepcopy(dict(cost_basis))
            _require_rfc3339(normalized["recorded_at"], label="recorded_at")
            return ceiling, normalized
        pricing = snapshot.get("pricing")
        if isinstance(pricing, Mapping):
            ceiling = pricing.get("credit_ceiling_per_attempt")
            if isinstance(ceiling, int) and not isinstance(ceiling, bool) and ceiling > 0 and pricing.get("source") and pricing.get("captured_at"):
                captured_at = _require_rfc3339(pricing["captured_at"], label="captured_at")
                price_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).astimezone(timezone.utc)
                snapshot_time = datetime.fromisoformat(
                    _require_rfc3339(snapshot.get("captured_at"), label="snapshot captured_at").replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                current = self._now()
                if isinstance(current, str):
                    current = datetime.fromisoformat(
                        _require_rfc3339(current, label="now").replace("Z", "+00:00")
                    )
                current = current.astimezone(timezone.utc)
                future_skew = timedelta(minutes=5)
                if price_time > current + future_skew or current - price_time > self._snapshot_max_age:
                    raise CostBasisError("pricing captured_at is stale or from the future")
                if price_time > snapshot_time + future_skew or snapshot_time - price_time > self._snapshot_max_age:
                    raise CostBasisError("pricing captured_at is inconsistent with snapshot captured_at")
                return ceiling, {
                    "kind": "live_snapshot", "credit_ceiling": ceiling, "currency": "credits",
                    "source": pricing["source"], "recorded_at": captured_at,
                }
        raise CostBasisError("live machine-readable price unavailable; explicit operator ceiling required")
