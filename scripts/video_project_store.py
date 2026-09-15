"""Private, versioned local storage for reference-video projects."""

from __future__ import annotations

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
                    f"v{max(numbers):03d}" if numbers else None
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

    def reserve_version(
        self, project_id: str, family: str, payload: Mapping[str, Any], *, schema_name: str,
        operation_id: str,
    ) -> dict[str, Any]:
        """Durably reserve one exact create-only version without publishing it."""
        if FAMILY.fullmatch(family) is None or re.fullmatch(r"[a-f0-9]{32,64}", operation_id) is None:
            raise ValueError("invalid reservation identity")
        with self._exclusive_lock(project_id):
            self.get(project_id)
            root = self.project_root(project_id)
            family_root = root / family
            family_root.mkdir(mode=0o700, exist_ok=True)
            numbers = [int(match.group(1)) for path in family_root.glob("v*.json")
                       if (match := re.fullmatch(r"v([0-9]{3,})", path.stem))]
            version = f"v{max(numbers, default=0) + 1:03d}"
            document = dict(payload); document["version"] = version
            validate_contract(document, schema_name)
            record = {"operation_id": operation_id, "family": family, "version": version,
                      "payload_fingerprint": canonical_fingerprint(document), "schema_name": schema_name}
            reservations = root / ".reservations"
            reservations.mkdir(mode=0o700, exist_ok=True)
            target = reservations / f"{operation_id}.json"
            if target.exists():
                existing = json.loads(target.read_text(encoding="utf-8"))
                if existing != record:
                    raise VersionReconciliationError(project_id=project_id, family=family, version=version,
                        path=target, reason="reservation differs from exact operation")
                return {**record, "payload": document}
            self._atomic_write(target, record)
            return {**record, "payload": document}

    def publish_reserved_version(self, project_id: str, reservation: Mapping[str, Any]) -> dict[str, Any]:
        """Publish exactly a sealed reservation, never allocating a replacement version."""
        operation_id = reservation.get("operation_id")
        if not isinstance(operation_id, str):
            raise ValueError("reservation operation identity is absent")
        with self._exclusive_lock(project_id):
            target = self.project_root(project_id) / ".reservations" / f"{operation_id}.json"
            try:
                sealed = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise VersionReconciliationError(project_id=project_id, family=str(reservation.get("family")),
                    version=str(reservation.get("version")), path=target, reason="sealed reservation unavailable") from exc
            if any(sealed.get(key) != reservation.get(key) for key in ("operation_id", "family", "version", "payload_fingerprint", "schema_name")):
                raise VersionReconciliationError(project_id=project_id, family=sealed["family"], version=sealed["version"],
                    path=target, reason="reservation was forged or changed")
            payload = dict(reservation.get("payload") or {})
            if canonical_fingerprint(payload) != sealed["payload_fingerprint"]:
                raise VersionReconciliationError(project_id=project_id, family=sealed["family"], version=sealed["version"],
                    path=target, reason="reserved payload fingerprint differs")
            return self.write_version(project_id, sealed["family"], payload, schema_name=sealed["schema_name"], version=sealed["version"])

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
            with os.fdopen(descriptor, "wb") as handle:
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
    "VersionCommitIndeterminateError",
    "VersionReconciliationError",
    "VideoProjectStore",
    "VideoProjectStoreError",
]
