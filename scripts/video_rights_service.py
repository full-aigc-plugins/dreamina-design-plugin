"""Record fail-closed, candidate-bound user rights assertions."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from scripts.json_contracts import ContractValidationError, canonical_fingerprint, validate_contract
from scripts.video_project_store import VideoProjectStore
from scripts.video_project_store import (
    VersionCommitIndeterminateError,
    VersionReconciliationError,
)


REUSE_DIMENSIONS = frozenset({
    "timing", "shot_sizes", "camera_moves", "rhythm", "transitions", "audio_beats",
    "likeness", "voice", "dialogue", "music", "brand", "artwork", "distinctive_props",
})
DISCLAIMER = (
    "User-supplied assertion recorded as engineering authorization evidence; "
    "not ownership verification or legal advice."
)


class RightsScopeError(PermissionError):
    """A receipt is absent, expired, narrower than requested, or differently bound."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _instant(value: str) -> datetime:
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z",
        value,
    ) is None:
        raise ContractValidationError("expiry must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError("expiry must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractValidationError("expiry must include a timezone")
    return parsed.astimezone(timezone.utc)


class VideoRightsService:
    """Persist user assertions only after native confirmation of the exact candidate."""

    def __init__(self, project_store: VideoProjectStore, native_confirmer: Any, *, now: Callable[[], str] = _utc_now) -> None:
        self._store = project_store
        self._confirmer = native_confirmer
        self._now = now

    def record_assertion(
        self,
        project_id: str,
        source_receipt: Mapping[str, Any],
        design_candidate: Mapping[str, Any],
        assertion: Mapping[str, Any],
        *,
        indeterminate_commit: VersionCommitIndeterminateError | None = None,
    ) -> dict[str, Any]:
        try:
            assertion_copy = json.loads(
                json.dumps(dict(assertion), ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ContractValidationError("rights assertion is not canonical JSON") from exc
        project = self._store.get(project_id)
        source_sha256 = source_receipt.get("source_sha256")
        fingerprint = design_candidate.get("design_fingerprint")
        if (
            source_receipt.get("project_id") != project_id
            or design_candidate.get("project_id") != project_id
            or design_candidate.get("source_sha256") != source_sha256
            or project["creative_mode"] != "authorized_replication"
        ):
            raise ContractValidationError("rights assertion is not bound to this replication project")
        if design_candidate.get("creative_mode") != "authorized_replication":
            raise ContractValidationError("rights assertion requires an authorized replication candidate")
        candidate_core = {
            key: value for key, value in design_candidate.items() if key != "design_fingerprint"
        }
        if canonical_fingerprint(candidate_core) != fingerprint:
            raise ContractValidationError("design candidate changed after fingerprinting")
        self._require_prepared_candidate(project_id, design_candidate)
        try:
            from scripts.video_redesign_service import VideoRedesignService

            redesign = VideoRedesignService(self._store)
            redesign._validate_candidate_against_current_evidence(design_candidate)
            redesign._validate_closed_candidate(design_candidate)
        except (ContractValidationError, KeyError, PermissionError, ValueError) as exc:
            raise ContractValidationError("design candidate is not currently valid") from exc
        if re.fullmatch(r"[a-f0-9]{64}", str(source_sha256)) is None or re.fullmatch(r"[a-f0-9]{64}", str(fingerprint)) is None:
            raise ContractValidationError("rights assertion requires exact source and candidate fingerprints")
        required = {"declarant", "rights_basis", "evidence", "allowed_media", "allowed_reuse", "purpose", "audience", "territory", "expires_at"}
        if set(assertion_copy) != required:
            raise ContractValidationError("rights assertion fields must be complete and closed")
        asserted_at = self._now()
        asserted = _instant(asserted_at)
        expires = _instant(assertion_copy["expires_at"])
        if expires <= asserted:
            raise ContractValidationError("rights expiry must be later than assertion time")
        confirmation_request = {
            "action": "assert-video-replication-rights", "project_id": project_id,
            "source_sha256": source_sha256, "creative_mode": "authorized_replication",
            "design_fingerprint": fingerprint, **assertion_copy, "disclaimer": DISCLAIMER,
        }
        if indeterminate_commit is not None:
            return self._reconcile_indeterminate(
                project_id, indeterminate_commit, confirmation_request
            )
        # Validate before showing or persisting by supplying only the storage-generated fields.
        provisional = {
            "schema_version": "1.0", "version": "v001",
            "receipt_id": "rr_" + canonical_fingerprint({**confirmation_request, "asserted_at": asserted_at})[:24],
            **{key: value for key, value in confirmation_request.items() if key != "action"},
            "asserted_at": asserted_at, "native_confirmation": "native-video-rights-confirmed",
        }
        validate_contract(provisional, "video_rights_receipt.schema.json")
        confirmed_bytes = json.dumps(
            confirmation_request, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        confirmation = self._confirmer.confirm_video_rights(confirmation_request)
        if confirmation != "native-video-rights-confirmed":
            raise RightsScopeError("native rights confirmation was not granted")
        if json.dumps(
            confirmation_request, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8") != confirmed_bytes:
            raise RightsScopeError("native confirmer changed the exact rights request")
        provisional["native_confirmation"] = confirmation
        return self._store.write_version(project_id, "rights_receipt", {key: value for key, value in provisional.items() if key != "version"}, schema_name="video_rights_receipt.schema.json")

    def _require_prepared_candidate(
        self, project_id: str, candidate: Mapping[str, Any]
    ) -> None:
        fingerprint = candidate["design_fingerprint"]
        root = self._store.project_root(project_id) / "prepared_candidate"
        filename = f"{fingerprint}.json"
        descriptors: list[int] = []
        try:
            root_descriptor = os.open(
                root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            descriptors.append(root_descriptor)
            root_meta = os.fstat(root_descriptor)
            target_descriptor = os.open(
                filename,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_descriptor,
            )
            descriptors.append(target_descriptor)
            target_meta = os.fstat(target_descriptor)
            if (
                not stat.S_ISDIR(root_meta.st_mode)
                or root_meta.st_mode & 0o777 != 0o700
                or not stat.S_ISREG(target_meta.st_mode)
                or target_meta.st_mode & 0o777 != 0o600
            ):
                raise OSError("prepared candidate storage is not private and regular")
            read_descriptor = os.dup(target_descriptor)
            with os.fdopen(read_descriptor, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
            current = os.stat(filename, dir_fd=root_descriptor, follow_symlinks=False)
            if (current.st_dev, current.st_ino, current.st_size, current.st_mode) != (
                target_meta.st_dev, target_meta.st_ino, target_meta.st_size, target_meta.st_mode
            ):
                raise OSError("prepared candidate changed during verification")
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractValidationError("candidate was not prepared by this project") from exc
        finally:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if stored != candidate:
            raise ContractValidationError("candidate differs from its prepared artifact")

    def _reconcile_indeterminate(
        self,
        project_id: str,
        commit: VersionCommitIndeterminateError,
        confirmation_request: Mapping[str, Any],
    ) -> dict[str, Any]:
        expected_path = (
            self._store.project_root(project_id)
            / "rights_receipt"
            / f"{commit.version}.json"
        )
        if (
            commit.project_id != project_id
            or commit.family != "rights_receipt"
            or commit.path != expected_path
        ):
            raise VersionReconciliationError(
                project_id=project_id,
                family="rights_receipt",
                version=commit.version,
                path=expected_path,
                reason="retry does not identify the exact indeterminate rights commit",
            )
        receipt = self._store.reconcile_version(
            project_id,
            "rights_receipt",
            commit.version,
            commit.payload_fingerprint,
            "video_rights_receipt.schema.json",
        )
        expected = {key: value for key, value in confirmation_request.items() if key != "action"}
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise VersionReconciliationError(
                project_id=project_id,
                family="rights_receipt",
                version=commit.version,
                path=expected_path,
                reason="retry assertion does not match the committed rights receipt",
            )
        return receipt

    def assert_scope(self, receipt: Mapping[str, Any], *, required: set[str], binding: Mapping[str, Any]) -> None:
        try:
            validate_contract(receipt, "video_rights_receipt.schema.json")
            exact = all(receipt.get(key) == binding.get(key) for key in ("project_id", "source_sha256", "creative_mode", "design_fingerprint"))
            allowed = set(receipt["allowed_reuse"])
            required_media_value = binding.get("required_media", ())
            if isinstance(required_media_value, (str, bytes)):
                raise TypeError("required media must be a collection")
            required_media = set(required_media_value)
            media_covered = required_media <= set(receipt["allowed_media"])
            context_covered = all(
                key not in binding or binding[key] == receipt[key]
                for key in ("purpose", "audience", "territory")
            )
            valid_required = required <= REUSE_DIMENSIONS
            current = _instant(self._now())
            expiry = _instant(receipt["expires_at"])
        except (ContractValidationError, KeyError, TypeError, ValueError) as exc:
            raise RightsScopeError("rights receipt is invalid") from exc
        if receipt["creative_mode"] != "authorized_replication" or not exact or not valid_required or not required <= allowed or not media_covered or not context_covered or expiry <= current:
            raise RightsScopeError("rights receipt does not cover requested reuse and binding")


__all__ = ["DISCLAIMER", "REUSE_DIMENSIONS", "RightsScopeError", "VideoRightsService"]
