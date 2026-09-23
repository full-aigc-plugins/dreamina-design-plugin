"""Private, versioned local storage for reference-video projects."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.json_contracts import canonical_fingerprint, validate_contract

PROJECT_ID = re.compile(r"^vp_[a-f0-9]{24}$")
ALLOWED_TRANSITIONS = {
    "created": {"analyzing", "blocked"},
    "analyzing": {"analysis_review", "blocked"},
    "analysis_review": {"analyzing", "designing", "blocked"},
    "designing": {"design_review", "blocked"},
    "design_review": {"designing", "quoted", "blocked"},
    "quoted": {"awaiting_approval", "designing", "blocked"},
    "awaiting_approval": {"generating", "quoted", "blocked"},
    "generating": {"evaluating", "manual_review", "blocked"},
    "evaluating": {"generating", "composing", "manual_review", "blocked"},
    "composing": {"final_review", "blocked"},
    "final_review": {"composing", "completed", "blocked"},
    "blocked": {"analyzing", "designing", "quoted", "generating", "composing"},
    "manual_review": {"generating", "evaluating", "blocked"},
    "completed": set(),
}
FAMILY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class VideoProjectStoreError(RuntimeError):
    """Base failure for private project storage."""


class ProjectNotFoundError(VideoProjectStoreError):
    """The requested project does not exist."""


class ProjectStateConflictError(VideoProjectStoreError):
    """A compare-and-swap state transition did not match exactly."""


class PendingVersionRecoveryRequired(VideoProjectStoreError):
    """A linear version chain must finish its earlier reserved operation first."""

    def __init__(self, *, project_id: str, family: str, version: str, operation_id: str):
        self.project_id = project_id
        self.family = family
        self.version = version
        self.operation_id = operation_id
        super().__init__(f"recover pending {project_id}/{family}/{version} operation {operation_id} before extending its parent chain")


class VersionCommitIndeterminateError(VideoProjectStoreError):
    """A version became visible but a later durability operation failed."""

    def __init__(
        self,
        *,
        project_id: str,
        family: str,
        version: str,
        path: Path,
        payload_fingerprint: str,
    ) -> None:
        self.project_id = project_id
        self.family = family
        self.version = version
        self.path = path
        self.payload_fingerprint = payload_fingerprint
        super().__init__(
            f"version commit is indeterminate after publication: {project_id}/{family}/{version}"
        )


class VersionReconciliationError(VideoProjectStoreError):
    """An exact committed version cannot be safely reconciled."""

    def __init__(self, *, project_id: str, family: str, version: str, path: Path, reason: str) -> None:
        self.project_id = project_id
        self.family = family
        self.version = version
        self.path = path
        self.reason = reason
        super().__init__(f"version reconciliation blocked: {reason}")


class VideoProjectStore:
    """Persist closed project documents and immutable numbered artifacts."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)

    def create(self, *, title: str, creative_mode: str, audio_policy: str) -> dict[str, Any]:
        now = _now()
        payload = {
            "schema_version": "1.0",
            "project_id": "vp_" + secrets.token_hex(12),
            "title": title,
            "creative_mode": creative_mode,
            "audio_policy": audio_policy,
            "state": "created",
            "created_at": now,
            "updated_at": now,
            "history": [{"state": "created", "recorded_at": now, "evidence": {}}],
        }
        validate_contract(payload, "video_project.schema.json")
        project_root = self._root / payload["project_id"]
        project_root.mkdir(mode=0o700)
        self._atomic_write(project_root / "project.json", payload)
        return payload

    def get(self, project_id: str) -> dict[str, Any]:
        path = self._project_path(project_id)
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            raise ProjectNotFoundError(project_id) from exc
        validate_contract(payload, "video_project.schema.json")
        return payload

    def list_projects(self, limit: int) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        projects = [self.get(path.name) for path in self._root.glob("vp_*") if path.is_dir()]
        projects.sort(key=lambda item: (item["updated_at"], item["project_id"]), reverse=True)
        return projects[:limit]

    def transition(
        self,
        project_id: str,
        *,
        expected: str,
        next_state: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        if expected not in ALLOWED_TRANSITIONS or next_state not in ALLOWED_TRANSITIONS:
            raise ProjectStateConflictError("unknown project state")
        with self._exclusive_lock(project_id):
            project = self.get(project_id)
            if project["state"] != expected:
                raise ProjectStateConflictError(f"expected {expected}, got {project['state']}")
            if next_state not in ALLOWED_TRANSITIONS[expected]:
                raise ProjectStateConflictError(f"transition {expected} -> {next_state} is forbidden")
            now = _now()
            project["state"] = next_state
            project["updated_at"] = now
            project["history"].append(
                {"state": next_state, "recorded_at": now, "evidence": dict(evidence)}
            )
            validate_contract(project, "video_project.schema.json")
            self._atomic_write(self._project_path(project_id), project)
            return project

    def write_version(
        self,
        project_id: str,
        family: str,
        payload: Mapping[str, Any],
        *,
        schema_name: str | None = None,
        parent_version_field: str | None = None,
        version: str | None = None,
        project_fd: int | None = None,
        publication_guard=None,
    ) -> dict[str, Any]:
        if FAMILY.fullmatch(family) is None:
            raise ValueError("invalid version family")
        if project_fd is not None:
            return self._write_version_at(project_fd, project_id, family, payload, schema_name, version, publication_guard)
        with self._exclusive_lock(project_id):
            self.get(project_id)
            family_root = self.project_root(project_id) / family
            family_root.mkdir(mode=0o700, exist_ok=True)
            os.chmod(family_root, 0o700)
            numbers = [
                int(match.group(1))
                for path in family_root.glob("v*.json")
                if (match := re.fullmatch(r"v([0-9]{3,})", path.stem)) is not None
            ]
            visible_numbers = list(numbers)
            if version is None or parent_version_field is not None:
                from scripts import reelbench_workspace as ws
                with ws.absolute_chain(self._root) as (root_fd, _, _), ws.directory(root_fd, project_id) as project_fd:
                    if parent_version_field is not None:
                        self._assert_no_pending_parent(project_id, project_fd, family)
                    if version is None:
                        numbers += self._reserved_numbers_at(project_fd, family)
            if version is None:
                version = f"v{max(numbers, default=0) + 1:03d}"
            elif re.fullmatch(r"v[0-9]{3,}", version) is None:
                raise ValueError("invalid requested version")
            document = dict(payload)
            document["version"] = version
            if parent_version_field is not None:
                if not isinstance(parent_version_field, str) or not parent_version_field:
                    raise ValueError("invalid parent version field")
                document[parent_version_field] = (
                    f"v{max(visible_numbers):03d}" if visible_numbers else None
                )
            if schema_name is not None:
                validate_contract(document, schema_name)
            target = family_root / f"{version}.json"
            indeterminate = VersionCommitIndeterminateError(
                project_id=project_id,
                family=family,
                version=version,
                path=target,
                payload_fingerprint=canonical_fingerprint(document),
            )
            self._atomic_write(target, document, indeterminate_error=indeterminate)
            return document

    def write_named_version(
        self,
        project_id: str,
        family: str,
        payload: Mapping[str, Any],
        *,
        version_field: str,
        schema_name: str,
    ) -> dict[str, Any]:
        """Persist a versioned document whose closed schema names its version field."""
        if FAMILY.fullmatch(family) is None or re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}", version_field
        ) is None:
            raise ValueError("invalid version family or field")
        with self._exclusive_lock(project_id):
            self.get(project_id)
            family_root = self.project_root(project_id) / family
            family_root.mkdir(mode=0o700, exist_ok=True)
            os.chmod(family_root, 0o700)
            numbers = [
                int(match.group(1))
                for path in family_root.glob("v*.json")
                if (match := re.fullmatch(r"v([0-9]{3,})", path.stem)) is not None
            ]
            version = f"v{max(numbers, default=0) + 1:03d}"
            document = dict(payload)
            claimed = document.get(version_field)
            if claimed is not None and claimed != version:
                raise ValueError(f"{version_field} does not match the next durable version")
            document[version_field] = version
            validate_contract(document, schema_name)
            target = family_root / f"{version}.json"
            indeterminate = VersionCommitIndeterminateError(
                project_id=project_id,
                family=family,
                version=version,
                path=target,
                payload_fingerprint=canonical_fingerprint(document),
            )
            self._atomic_write(target, document, indeterminate_error=indeterminate)
            return document
    def reserve_version(
        self, project_id: str, family: str, payload: Mapping[str, Any], *, schema_name: str,
        operation_id: str, parent_version_field: str | None = None,
        project_fd: int | None = None, publication_guard=None,
        fingerprint_field: str | None = None,
    ) -> dict[str, Any]:
        """Reserve exact bytes and version; pending reservations occupy version numbers."""
        if FAMILY.fullmatch(family) is None or re.fullmatch(r"[a-f0-9]{32,64}", operation_id) is None:
            raise ValueError("invalid reservation identity")
        if project_fd is None:
            from scripts.reelbench_project_service import ReelBenchProjectService
            with ReelBenchProjectService(self, None)._locked_project(project_id) as (_, fd, _, guard):
                return self.reserve_version(project_id, family, payload, schema_name=schema_name,
                    operation_id=operation_id, parent_version_field=parent_version_field,
                    project_fd=fd, publication_guard=guard, fingerprint_field=fingerprint_field)
        from scripts import reelbench_workspace as ws
        with self._reservation_lock(project_fd):
            ws.mkdir(project_fd, ".reservations")
            request_fingerprint = canonical_fingerprint({"family": family, "payload": dict(payload),
                "schema_name": schema_name, "parent_version_field": parent_version_field,
                "fingerprint_field": fingerprint_field})
            try:
                existing = self._read_reservation_at(project_fd, operation_id)
            except FileNotFoundError:
                existing = None
            if parent_version_field is not None:
                self._assert_no_pending_parent(project_id, project_fd, family,
                    operation_id=operation_id, version=existing["version"] if existing is not None else None)
            if existing is not None:
                if existing["request_fingerprint"] != request_fingerprint:
                    raise ValueError("reservation differs from exact operation")
                self._sync_reservation_at(project_fd, operation_id)
                return existing
            ws.mkdir(project_fd, family)
            with ws.directory(project_fd, family) as fd:
                visible = [int(name[1:-5]) for name in os.listdir(fd)
                           if re.fullmatch(r"v[0-9]{3,}\.json", name)]
            numbers = visible + self._reserved_numbers_at(project_fd, family)
            version = f"v{max(numbers, default=0) + 1:03d}"
            document = dict(payload)
            document["version"] = version
            if parent_version_field is not None:
                document[parent_version_field] = f"v{max(visible):03d}" if visible else None
            if fingerprint_field is not None:
                document[fingerprint_field] = canonical_fingerprint({k: v for k, v in document.items() if k != fingerprint_field})
            validate_contract(document, schema_name)
            record = {"operation_id": operation_id, "family": family, "version": version,
                      "payload_fingerprint": canonical_fingerprint(document), "schema_name": schema_name,
                      "request_fingerprint": request_fingerprint, "payload": document}
            record["seal"] = self._reservation_signature(project_fd, record)
            if publication_guard is not None:
                publication_guard()
            temporary = f".reservations/.reserve-{secrets.token_hex(12)}"
            try:
                ws.write(project_fd, temporary, json.dumps(record, sort_keys=True).encode())
                os.link(temporary, f".reservations/{operation_id}.json", src_dir_fd=project_fd,
                        dst_dir_fd=project_fd, follow_symlinks=False)
                with ws.directory(project_fd, ".reservations") as fd:
                    os.fsync(fd)
            finally:
                primary = sys.exc_info()[1]
                try:
                    os.unlink(temporary, dir_fd=project_fd)
                except FileNotFoundError:
                    pass
                except BaseException as exc:
                    ws.cleanup_failure(primary, exc)
            return record

    def publish_reserved_version(self, project_id: str, reservation: Mapping[str, Any], *,
                                 project_fd: int | None = None, publication_guard=None) -> dict[str, Any]:
        """Publish exactly the reserved version without reacquiring the project lock."""
        operation_id = reservation.get("operation_id")
        if not isinstance(operation_id, str) or re.fullmatch(r"[a-f0-9]{32,64}", operation_id) is None:
            raise ValueError("reservation operation identity is absent")
        if project_fd is None:
            from scripts.reelbench_project_service import ReelBenchProjectService
            with ReelBenchProjectService(self, None)._locked_project(project_id) as (_, fd, _, guard):
                return self.publish_reserved_version(project_id, reservation, project_fd=fd, publication_guard=guard)
        with self._reservation_lock(project_fd):
            sealed = self._read_reservation_at(project_fd, operation_id)
            if sealed != reservation:
                raise ValueError("reservation was forged or changed")
            return self.write_version(project_id, sealed["family"], sealed["payload"],
                schema_name=sealed["schema_name"], version=sealed["version"], project_fd=project_fd,
                publication_guard=publication_guard)

    def _read_reservation_at(self, project_fd, operation_id):
        from scripts import reelbench_workspace as ws
        if not isinstance(operation_id, str) or re.fullmatch(r"[a-f0-9]{32,64}", operation_id) is None:
            raise ValueError("invalid reservation identity")
        name = f".reservations/{operation_id}.json"
        with ws.file_at(project_fd, name) as fd:
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
                raise ValueError("reservation must be private")
        record = json.loads(ws.read(project_fd, name))
        if (set(record) != {"operation_id", "family", "version", "payload_fingerprint", "schema_name", "request_fingerprint", "payload", "seal"}
            or record["operation_id"] != operation_id or FAMILY.fullmatch(record["family"]) is None
            or re.fullmatch(r"v[0-9]{3,}", record["version"]) is None
            or record["payload"].get("version") != record["version"]
            or canonical_fingerprint(record["payload"]) != record["payload_fingerprint"]):
            raise ValueError("invalid reservation record")
        if not isinstance(record["seal"], str) or not hmac.compare_digest(record["seal"], self._reservation_signature(project_fd, record)):
            raise ValueError("reservation seal differs")
        validate_contract(record["payload"], record["schema_name"])
        return record

    def _reservation_signature(self, project_fd, record):
        from scripts import reelbench_workspace as ws
        from scripts.reelbench_operation import OperationJournal
        info = os.fstat(project_fd)
        body = {k: v for k, v in record.items() if k != "seal"}
        body["project_identity"] = [info.st_dev, info.st_ino]
        with ws.absolute_chain(self._root) as (store_fd, _, _):
            key = OperationJournal(store_fd, project_fd, "", self._root).key
        return hmac.new(key, canonical_fingerprint(body).encode(), hashlib.sha256).hexdigest()

    def _reservations_at(self, project_fd, family):
        from scripts import reelbench_workspace as ws
        try:
            with ws.directory(project_fd, ".reservations") as fd:
                names = os.listdir(fd)
        except FileNotFoundError:
            return []
        records = [self._read_reservation_at(project_fd, name[:-5]) for name in names
                   if re.fullmatch(r"[a-f0-9]{32,64}\.json", name)]
        return [record for record in records if record["family"] == family]

    def _reserved_numbers_at(self, project_fd, family):
        return [int(record["version"][1:]) for record in self._reservations_at(project_fd, family)]

    def _assert_no_pending_parent(self, project_id, project_fd, family, *, operation_id=None, version=None):
        for pending in sorted(self._reservations_at(project_fd, family), key=lambda r: int(r["version"][1:])):
            if pending["operation_id"] == operation_id:
                continue
            if version is None or int(pending["version"][1:]) <= int(version[1:]):
                raise PendingVersionRecoveryRequired(project_id=project_id, family=family,
                    version=pending["version"], operation_id=pending["operation_id"])

    @staticmethod
    def _sync_reservation_at(project_fd, operation_id):
        from scripts import reelbench_workspace as ws
        with ws.file_at(project_fd, f".reservations/{operation_id}.json") as fd:
            os.fsync(fd)
        with ws.directory(project_fd, ".reservations") as fd:
            os.fsync(fd)
        os.fsync(project_fd)

    @staticmethod
    @contextmanager
    def _reservation_lock(project_fd):
        fd = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=project_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise ValueError("project lock must be private")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def reconcile_reserved_version(self, project_id: str, reservation: Mapping[str, Any]) -> dict[str, Any]:
        """Read only the exact durable subject reserved by one operation."""
        return self.reconcile_version(project_id, reservation["family"], reservation["version"],
                                      reservation["payload_fingerprint"], reservation["schema_name"])

    def reconcile_version(
        self,
        project_id: str,
        family: str,
        version: str,
        expected_fingerprint: str,
        schema_name: str | None,
    ) -> dict[str, Any]:
        """Read and validate one exact version without allocating or mutating storage."""
        if FAMILY.fullmatch(family) is None or re.fullmatch(r"v[0-9]{3,}", version) is None:
            raise ValueError("invalid version identity")
        if re.fullmatch(r"[a-f0-9]{64}", expected_fingerprint) is None:
            raise ValueError("invalid expected fingerprint")
        self._validate_project_id(project_id)
        target = self._root / project_id / family / f"{version}.json"
        descriptors: list[int] = []
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            root_descriptor = os.open(self._root, directory_flags)
            descriptors.append(root_descriptor)
            root_metadata = self._require_directory(root_descriptor, 0o700, "store root")

            project_descriptor = os.open(project_id, directory_flags, dir_fd=root_descriptor)
            descriptors.append(project_descriptor)
            project_metadata = self._require_directory(
                project_descriptor, 0o700, "project directory"
            )

            family_descriptor = os.open(family, directory_flags, dir_fd=project_descriptor)
            descriptors.append(family_descriptor)
            family_metadata = self._require_directory(
                family_descriptor, 0o700, "version family"
            )

            file_descriptor = os.open(
                f"{version}.json",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=family_descriptor,
            )
            descriptors.append(file_descriptor)
            file_metadata = os.fstat(file_descriptor)
            if not stat.S_ISREG(file_metadata.st_mode):
                raise OSError("version is not a regular file")
            if file_metadata.st_mode & 0o777 != 0o600:
                raise OSError("version is not private")

            read_descriptor = os.dup(file_descriptor)
            try:
                with os.fdopen(read_descriptor, "r", encoding="utf-8") as handle:
                    read_descriptor = -1
                    document = json.load(handle)
            finally:
                if read_descriptor >= 0:
                    os.close(read_descriptor)
            if not isinstance(document, dict) or document.get("version") != version:
                raise ValueError("version token mismatch")
            if schema_name is not None:
                validate_contract(document, schema_name)
            if canonical_fingerprint(document) != expected_fingerprint:
                raise ValueError("payload fingerprint mismatch")

            # Re-open every pathname from its pinned parent. Any replacement,
            # even one containing identical bytes, makes reconciliation unsafe.
            self._require_same_identity(
                os.stat(self._root, follow_symlinks=False), root_metadata, "store root"
            )
            self._require_same_identity(
                os.stat(project_id, dir_fd=root_descriptor, follow_symlinks=False),
                project_metadata,
                "project directory",
            )
            self._require_same_identity(
                os.stat(family, dir_fd=project_descriptor, follow_symlinks=False),
                family_metadata,
                "version family",
            )
            self._require_same_identity(
                os.stat(
                    f"{version}.json", dir_fd=family_descriptor, follow_symlinks=False
                ),
                file_metadata,
                "version",
            )
            return document
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise VersionReconciliationError(
                project_id=project_id,
                family=family,
                version=version,
                path=target,
                reason=str(exc),
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def find_version_by_field(
        self,
        project_id: str,
        family: str,
        *,
        field: str,
        value: str,
        schema_name: str,
    ) -> dict[str, Any]:
        """Find one immutable version through pinned private descriptors."""
        if FAMILY.fullmatch(family) is None or not isinstance(field, str) or not field:
            raise ValueError("invalid version lookup")
        self._validate_project_id(project_id)
        target = self._root / project_id / family
        descriptors: list[int] = []
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            root_descriptor = os.open(self._root, directory_flags)
            descriptors.append(root_descriptor)
            root_metadata = self._require_directory(root_descriptor, 0o700, "store root")
            project_descriptor = os.open(project_id, directory_flags, dir_fd=root_descriptor)
            descriptors.append(project_descriptor)
            project_metadata = self._require_directory(project_descriptor, 0o700, "project directory")
            family_descriptor = os.open(family, directory_flags, dir_fd=project_descriptor)
            descriptors.append(family_descriptor)
            family_metadata = self._require_directory(family_descriptor, 0o700, "version family")
            matches: list[tuple[dict[str, Any], str, os.stat_result]] = []
            for name in sorted(os.listdir(family_descriptor)):
                if re.fullmatch(r"v[0-9]{3,}\.json", name) is None:
                    continue
                file_descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=family_descriptor)
                try:
                    metadata = os.fstat(file_descriptor)
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o777 != 0o600:
                        raise OSError("version is not a private regular file")
                    with os.fdopen(os.dup(file_descriptor), "r", encoding="utf-8") as handle:
                        document = json.load(handle)
                    version = name[:-5]
                    if not isinstance(document, dict) or document.get("version") != version:
                        raise ValueError("version token mismatch")
                    validate_contract(document, schema_name)
                    if document.get(field) == value:
                        matches.append((document, name, metadata))
                finally:
                    os.close(file_descriptor)
            if len(matches) != 1:
                raise ValueError("version lookup did not resolve exactly one document")
            document, name, metadata = matches[0]
            self._require_same_identity(os.stat(self._root, follow_symlinks=False), root_metadata, "store root")
            self._require_same_identity(os.stat(project_id, dir_fd=root_descriptor, follow_symlinks=False), project_metadata, "project directory")
            self._require_same_identity(os.stat(family, dir_fd=project_descriptor, follow_symlinks=False), family_metadata, "version family")
            self._require_same_identity(os.stat(name, dir_fd=family_descriptor, follow_symlinks=False), metadata, "version")
            return document
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise VersionReconciliationError(
                project_id=project_id, family=family, version="lookup", path=target,
                reason=str(exc),
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def read_version(
        self, project_id: str, family: str, version: str, schema_name: str
    ) -> dict[str, Any]:
        """Read one exact version through the pinned descriptor safety boundary."""
        if re.fullmatch(r"v[0-9]{3,}", version) is None:
            raise ValueError("invalid version identity")
        return self.find_version_by_field(
            project_id,
            family,
            field="version",
            value=version,
            schema_name=schema_name,
        )

    @staticmethod
    def _require_directory(descriptor: int, mode: int, label: str) -> os.stat_result:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError(f"{label} is not a directory")
        if metadata.st_mode & 0o777 != mode:
            raise OSError(f"{label} is not private")
        return metadata

    @staticmethod
    def _require_same_identity(
        current: os.stat_result, expected: os.stat_result, label: str
    ) -> None:
        current_identity = (
            current.st_dev,
            current.st_ino,
            current.st_size,
            stat.S_IFMT(current.st_mode),
            current.st_mode & 0o777,
        )
        expected_identity = (
            expected.st_dev,
            expected.st_ino,
            expected.st_size,
            stat.S_IFMT(expected.st_mode),
            expected.st_mode & 0o777,
        )
        if current_identity != expected_identity:
            raise OSError(f"{label} changed during reconciliation")

    def project_root(self, project_id: str) -> Path:
        self._validate_project_id(project_id)
        root = self._root / project_id
        if not root.is_dir() or root.is_symlink():
            raise ProjectNotFoundError(project_id)
        return root

    def _project_path(self, project_id: str) -> Path:
        return self.project_root(project_id) / "project.json"

    @staticmethod
    def _validate_project_id(project_id: str) -> None:
        if not isinstance(project_id, str) or PROJECT_ID.fullmatch(project_id) is None:
            raise ProjectNotFoundError(str(project_id))

    @contextmanager
    def _exclusive_lock(self, project_id: str) -> Iterator[None]:
        lock_path = self.project_root(project_id) / ".lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _atomic_write(
        path: Path,
        payload: Mapping[str, Any],
        *,
        indeterminate_error: VersionCommitIndeterminateError | None = None,
    ) -> None:
        encoded = (json.dumps(dict(payload), sort_keys=True, ensure_ascii=False) + "\n").encode()
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        published = False
        try:
            os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "wb")
            descriptor = -1  # Ownership moved to the file object; never close a reused fd.
            with handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if indeterminate_error is not None:
                # Immutable receipts are create-only.  Mark visibility before
                # unlink/fsync so every post-link failure is recoverable.
                os.link(temporary, path, follow_symlinks=False)
                published = True
                os.unlink(temporary)
            else:
                os.replace(temporary, path)
                published = True
            os.chmod(path, 0o600)
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException as exc:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                os.unlink(temporary)
            except OSError:
                pass
            if published and indeterminate_error is not None:
                raise indeterminate_error from exc
            raise

    def _write_version_at(self, project_fd, project_id, family, payload, schema_name, version, publication_guard=None):
        """Create-only publication below a caller-owned, exclusively locked project inode."""
        from scripts import reelbench_workspace as ws
        self._validate_project_id(project_id)
        if version is None or re.fullmatch(r"v[0-9]{3,}", version) is None:
            raise ValueError("descriptor publication requires an exact version")
        document = dict(payload)
        document["version"] = version
        if schema_name is not None:
            validate_contract(document, schema_name)
        ws.mkdir(project_fd, family)
        error = VersionCommitIndeterminateError(project_id=project_id, family=family, version=version,
            path=self._root / project_id / family / f"{version}.json",
            payload_fingerprint=canonical_fingerprint(document))
        with ws.directory(project_fd, family) as fd:
            temporary = f".{version}.json.{secrets.token_hex(12)}"
            visible = False
            try:
                ws.write(fd, temporary, (json.dumps(document, sort_keys=True, ensure_ascii=False) + "\n").encode())
                if publication_guard is not None:
                    publication_guard()
                os.link(temporary, f"{version}.json", src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                visible = True
                os.unlink(temporary, dir_fd=fd)
                os.fsync(fd)
                os.fsync(project_fd)
            except BaseException as exc:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except OSError:
                    pass
                if visible:
                    raise error from exc
                raise
        return document



def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ALLOWED_TRANSITIONS",
    "PROJECT_ID",
    "ProjectNotFoundError",
    "ProjectStateConflictError",
    "PendingVersionRecoveryRequired",
    "VersionCommitIndeterminateError",
    "VersionReconciliationError",
    "VideoProjectStore",
    "VideoProjectStoreError",
]
