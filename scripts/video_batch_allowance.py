"""Private durable accounting for one approved Dreamina video batch."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from scripts.json_contracts import ContractValidationError, canonical_fingerprint, validate_contract
from scripts.video_generation_planner import PlanningError, quote_total, validate_batch_quote


class BatchScopeError(ValueError):
    """The quote, request, or durable state is outside the approved envelope."""


class BudgetExceededError(BatchScopeError):
    """Declared or consumed credits exceed the exact pre-enumerated ceiling."""


class AllowanceAlreadyActivatedError(BatchScopeError):
    """A quote fingerprint has already consumed its one activation approval."""


class ReservationConsumedError(BatchScopeError):
    """An exact shot attempt has already been irreversibly reserved."""


class AllowanceNotFoundError(BatchScopeError):
    """No durable allowance exists for the opaque identifier."""


class AllowanceCommitIndeterminateError(BatchScopeError):
    """A durable replace may have committed; reconcile state instead of retrying."""

    def __init__(self, allowance_id: str, operation: str) -> None:
        super().__init__(f"{operation} outcome is indeterminate for {allowance_id}; reconcile before continuing")
        self.allowance_id = allowance_id
        self.operation = operation


_ALLOWANCE_ID = re.compile(r"^ba_[a-f0-9]{32}$")
_RESERVATION_ID = re.compile(r"^br_[a-f0-9]{32}$")
_SUBMIT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ERROR_CODE = re.compile(r"^[A-Z0-9_-]{1,128}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VideoBatchAllowance:
    """Activate and irreversibly consume an exact, non-expandable quote."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._allowances = self._root / "allowances"
        self._activations = self._root / "activations"
        for directory in (self._root, self._allowances, self._activations):
            if directory.is_symlink():
                raise BatchScopeError("allowance directory must not be a symlink")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            metadata = directory.stat()
            if metadata.st_uid != os.getuid() or not stat.S_ISDIR(metadata.st_mode):
                raise BatchScopeError("allowance directory ownership or type is unsafe")
            os.chmod(directory, 0o700)

    def activate(self, quote: Mapping[str, Any], approver: Any) -> str:
        """Obtain native approval once and persist the exact batch allowance."""
        quote_copy = self._copy_json(quote)
        self._validate_quote_for_activation(quote_copy)
        quote_fingerprint = quote_copy["quote_fingerprint"]
        with self._exclusive_lock():
            activation_path = self._activations / f"{quote_fingerprint}.json"
            if activation_path.exists() or any(
                self._load_path(path)["quote_fingerprint"] == quote_fingerprint
                for path in self._allowances.glob("ba_*.json")
            ):
                raise AllowanceAlreadyActivatedError("quote approval was already activated")
            approval_request = self._approval_request(quote_copy)
            confirmation_request = copy.deepcopy(approval_request)
            approval_bytes = self._canonical_bytes(confirmation_request)
            if approver.confirm_video_batch(confirmation_request) != "native-video-batch-confirmed":
                raise BatchScopeError("native whole-batch approval was not granted")
            if self._canonical_bytes(confirmation_request) != approval_bytes:
                raise BatchScopeError("native approver changed the exact batch envelope")
            allowance_id = "ba_" + secrets.token_hex(16)
            timestamp = _now_iso()
            requests = self._flatten_requests(quote_copy)
            allowance: dict[str, Any] = {
                "schema_version": "1.0", "allowance_id": allowance_id, "state": "active",
                "project_id": quote_copy["project_id"], "quote_version": quote_copy["quote_version"],
                "quote_fingerprint": quote_fingerprint, "source_sha256": quote_copy["source_sha256"],
                "analysis_version": quote_copy["analysis_version"], "design_version": quote_copy["design_version"],
                "design_fingerprint": quote_copy["design_fingerprint"],
                "rights_receipt_id": quote_copy["rights_receipt_id"],
                "rights_binding_fingerprint": self._rights_binding(quote_copy),
                "creative_mode": quote_copy["creative_mode"], "audio_policy": quote_copy["audio_policy"],
                "destination": quote_copy["output_destination"],
                "output_profile": copy.deepcopy(quote_copy["output_profile"]),
                "capability_snapshot_fingerprint": quote_copy["capability_snapshot_fingerprint"],
                "total_credit_ceiling": quote_copy["total_credit_ceiling"], "consumed_credits": 0,
                "requests": requests, "reservations": [], "activated_at": timestamp,
                "history": [{"state": "active", "at": timestamp}],
            }
            self._seal(allowance)
            try:
                self._atomic_write(self._allowance_path(allowance_id), allowance, allowance_id=allowance_id, operation="activation")
                self._atomic_write(activation_path, {"allowance_id": allowance_id, "quote_fingerprint": quote_fingerprint}, allowance_id=allowance_id, operation="activation-index")
            except BatchScopeError:
                raise
            except Exception as exc:
                raise AllowanceCommitIndeterminateError(allowance_id, "activation") from exc
            return allowance_id

    def assert_quote(self, allowance_id: str, quote: Mapping[str, Any]) -> None:
        """Reject any quote other than the exact approved canonical quote."""
        allowance = self.get(allowance_id)
        quote_copy = self._copy_json(quote)
        try:
            validate_batch_quote(quote_copy)
        except (ContractValidationError, PlanningError, TypeError, ValueError) as exc:
            raise BatchScopeError("quote is invalid or outside the approved batch envelope") from exc
        if quote_copy["quote_fingerprint"] != allowance["quote_fingerprint"]:
            raise BatchScopeError("quote is outside the approved batch envelope")

    def reserve(self, allowance_id: str, *, shot_id: str, attempt: int, request_fingerprint: str) -> dict[str, Any]:
        """Consume one exact pre-enumerated reservation under an exclusive file lock."""
        if (
            not isinstance(shot_id, str)
            or isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or not isinstance(request_fingerprint, str)
        ):
            raise BatchScopeError("reservation tuple has invalid types")
        with self._exclusive_lock():
            allowance = self._load_allowance(allowance_id)
            item = next((entry for entry in allowance["requests"] if entry["request_fingerprint"] == request_fingerprint), None)
            if item is None or item["shot_id"] != shot_id or item["attempt"] != attempt:
                raise BatchScopeError("request is outside the approved batch envelope")
            if any(entry["request_fingerprint"] == request_fingerprint for entry in allowance["reservations"]):
                raise ReservationConsumedError("the exact request reservation is already consumed")
            new_total = allowance["consumed_credits"] + item["credit_ceiling"]
            if new_total > allowance["total_credit_ceiling"]:
                raise BudgetExceededError("reservation exceeds the approved credit ceiling")
            timestamp = _now_iso()
            reservation = {
                "reservation_id": "br_" + secrets.token_hex(16), "shot_id": shot_id,
                "attempt": attempt, "request_fingerprint": request_fingerprint,
                "credit_ceiling": item["credit_ceiling"], "state": "reserved", "reserved_at": timestamp,
                "history": [{"state": "reserved", "at": timestamp}],
            }
            allowance["reservations"].append(reservation)
            allowance["consumed_credits"] = new_total
            if len(allowance["reservations"]) == len(allowance["requests"]):
                allowance["state"] = "exhausted"
            allowance["history"].append({"state": "reserved", "at": timestamp, "reservation_id": reservation["reservation_id"]})
            self._seal(allowance)
            self._atomic_write(self._allowance_path(allowance_id), allowance, allowance_id=allowance_id, operation="reservation")
            return copy.deepcopy(reservation)

    def commit(self, reservation_id: str, submit_id: str) -> dict[str, Any]:
        """Bind a consumed reservation to the opaque provider submit identifier."""
        if not _RESERVATION_ID.fullmatch(reservation_id) or not isinstance(submit_id, str) or _SUBMIT_ID.fullmatch(submit_id) is None:
            raise BatchScopeError("invalid reservation or submit identifier")
        return self._transition_reservation(reservation_id, "committed", submit_id=submit_id)

    def mark_ambiguous(self, reservation_id: str, error_code: str) -> dict[str, Any]:
        """Irreversibly record an unknown result after the invocation boundary."""
        if not _RESERVATION_ID.fullmatch(reservation_id) or not isinstance(error_code, str) or _ERROR_CODE.fullmatch(error_code) is None:
            raise BatchScopeError("invalid reservation or ambiguity code")
        return self._transition_reservation(reservation_id, "ambiguous", error_code=error_code)

    def get(self, allowance_id: str) -> dict[str, Any]:
        """Return a validated copy of one durable allowance."""
        with self._exclusive_lock():
            return copy.deepcopy(self._load_allowance(allowance_id))

    def _transition_reservation(self, reservation_id: str, state: str, **details: str) -> dict[str, Any]:
        with self._exclusive_lock():
            for path in self._allowances.glob("ba_*.json"):
                allowance = self._load_path(path)
                reservation = next((item for item in allowance["reservations"] if item["reservation_id"] == reservation_id), None)
                if reservation is None:
                    continue
                submit_id = details.get("submit_id")
                if submit_id is not None and any(
                    item["reservation_id"] != reservation_id and item.get("submit_id") == submit_id
                    for item in allowance["reservations"]
                ):
                    raise ReservationConsumedError("submit identifier is already bound")
                if reservation["state"] != "reserved":
                    if reservation["state"] == state and all(reservation.get(key) == value for key, value in details.items()):
                        return copy.deepcopy(reservation)
                    raise ReservationConsumedError("reservation already crossed its terminal boundary")
                if state == "committed" and any(
                    other.get("submit_id") == details["submit_id"]
                    for candidate in self._allowances.glob("ba_*.json")
                    for other in self._load_path(candidate)["reservations"]
                    if other["reservation_id"] != reservation_id
                ):
                    raise ReservationConsumedError("submit identifier is already bound to another reservation")
                timestamp = _now_iso()
                reservation["state"] = state
                reservation.update(details)
                event = {"state": state, "at": timestamp, **details}
                reservation["history"].append(event)
                allowance["history"].append({"state": state, "at": timestamp, "reservation_id": reservation_id, **details})
                self._seal(allowance)
                self._atomic_write(path, allowance, allowance_id=allowance["allowance_id"], operation=state)
                return copy.deepcopy(reservation)
        raise AllowanceNotFoundError("reservation does not exist")

    def _validate_quote_for_activation(self, quote: Mapping[str, Any]) -> None:
        literal_total = quote_total(quote.get("items", [])) if isinstance(quote.get("items"), list) else -1
        if quote.get("total_credit_ceiling") != literal_total:
            raise BudgetExceededError("declared total does not equal request ceilings")
        try:
            validate_batch_quote(quote)
        except (ContractValidationError, PlanningError, TypeError, ValueError) as exc:
            raise BatchScopeError("invalid batch quote") from exc
        fingerprints = [
            attempt["request_fingerprint"]
            for item in quote["items"]
            for attempt in item["attempts"]
        ]
        if len(fingerprints) != len(set(fingerprints)):
            raise BatchScopeError("request fingerprints must be unique across the approved batch")
        if (quote["creative_mode"] == "authorized_replication") != (quote["rights_receipt_id"] is not None):
            raise BatchScopeError("rights receipt is not bound to the selected creative mode")

    @staticmethod
    def _flatten_requests(quote: Mapping[str, Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for item in quote["items"]:
            for attempt in item["attempts"]:
                request = attempt["request"]
                entries.append({
                    "shot_id": item["shot_id"], "attempt": attempt["attempt_number"], "mode": item["mode"],
                    "model": request.get("model"), "video_resolution": request["video_resolution"],
                    "duration_seconds": request["duration_seconds"],
                    "request_fingerprint": attempt["request_fingerprint"],
                    "credit_ceiling": attempt["credit_ceiling"],
                })
        return entries

    @classmethod
    def _approval_request(cls, quote: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "action": "activate-video-batch-allowance", "project_id": quote["project_id"],
            "quote_version": quote["quote_version"], "quote_fingerprint": quote["quote_fingerprint"],
            "source_sha256": quote["source_sha256"], "analysis_version": quote["analysis_version"],
            "design_version": quote["design_version"], "design_fingerprint": quote["design_fingerprint"],
            "rights_receipt_id": quote["rights_receipt_id"], "rights_binding_fingerprint": cls._rights_binding(quote),
            "creative_mode": quote["creative_mode"], "audio_policy": quote["audio_policy"],
            "shot_count": quote["item_count"], "task_count": quote["task_count"],
            "reserved_retry_count": quote["reserved_retry_count"],
            "total_credit_ceiling": quote["total_credit_ceiling"], "destination": quote["output_destination"],
            "output_profile": copy.deepcopy(quote["output_profile"]), "items": copy.deepcopy(quote["items"]),
        }

    @staticmethod
    def _rights_binding(quote: Mapping[str, Any]) -> str:
        explicit = quote.get("rights_receipt_fingerprint")
        if isinstance(explicit, str):
            return explicit
        return canonical_fingerprint({
            "creative_mode": quote.get("creative_mode"),
            "design_fingerprint": quote.get("design_fingerprint"),
            "project_id": quote.get("project_id"),
            "rights_receipt_id": quote.get("rights_receipt_id"),
            "source_sha256": quote.get("source_sha256"),
        })

    def _load_allowance(self, allowance_id: str) -> dict[str, Any]:
        return self._load_path(self._allowance_path(allowance_id))

    def _load_path(self, path: Path) -> dict[str, Any]:
        try:
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise BatchScopeError("allowance file ownership, type, or mode is unsafe")
            payload = json.loads(path.read_text(encoding="utf-8"))
        except BatchScopeError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise BatchScopeError("allowance durable state is missing or corrupt") from exc
        if not isinstance(payload, dict):
            raise BatchScopeError("allowance durable state is corrupt")
        fingerprint = payload.get("state_fingerprint")
        core = {key: value for key, value in payload.items() if key != "state_fingerprint"}
        if fingerprint != canonical_fingerprint(core):
            raise BatchScopeError("allowance durable state fingerprint mismatch")
        try:
            validate_contract(payload, "video_batch_allowance.schema.json")
        except ContractValidationError as exc:
            raise BatchScopeError("allowance durable state violates its contract") from exc
        if payload["consumed_credits"] != sum(item["credit_ceiling"] for item in payload["reservations"]):
            raise BatchScopeError("allowance consumed credits are corrupt")
        if payload["consumed_credits"] > payload["total_credit_ceiling"]:
            raise BudgetExceededError("allowance exceeds its approved ceiling")
        return payload

    def _allowance_path(self, allowance_id: str) -> Path:
        if not isinstance(allowance_id, str) or not _ALLOWANCE_ID.fullmatch(allowance_id):
            raise AllowanceNotFoundError("invalid allowance identifier")
        return self._allowances / f"{allowance_id}.json"

    @staticmethod
    def _copy_json(payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            copied = json.loads(json.dumps(dict(payload), ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError, OverflowError) as exc:
            raise BatchScopeError("batch quote is not canonical JSON") from exc
        return copied

    @staticmethod
    def _seal(payload: dict[str, Any]) -> None:
        payload.pop("state_fingerprint", None)
        payload["state_fingerprint"] = canonical_fingerprint(payload)

    def _atomic_write(self, path: Path, payload: Mapping[str, Any], *, allowance_id: str, operation: str) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        replaced = False
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            replaced = True
            os.chmod(path, 0o600)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception as exc:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            if replaced:
                raise AllowanceCommitIndeterminateError(allowance_id, operation) from exc
            raise

    @staticmethod
    def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
        return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        lock_path = self._root / ".allowance.lock"
        with open(lock_path, "a+b") as handle:
            os.chmod(lock_path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = [
    "AllowanceAlreadyActivatedError", "AllowanceCommitIndeterminateError", "AllowanceNotFoundError", "BatchScopeError",
    "BudgetExceededError", "ReservationConsumedError", "VideoBatchAllowance",
]
