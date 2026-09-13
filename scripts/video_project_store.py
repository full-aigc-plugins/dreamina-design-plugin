"""Private, versioned local storage for reference-video projects."""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from scripts.json_contracts import validate_contract


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
    ) -> dict[str, Any]:
        if FAMILY.fullmatch(family) is None:
            raise ValueError("invalid version family")
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
            document["version"] = version
            if schema_name is not None:
                validate_contract(document, schema_name)
            self._atomic_write(family_root / f"{version}.json", document)
            return document

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
    def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
        encoded = (json.dumps(dict(payload), sort_keys=True, ensure_ascii=False) + "\n").encode()
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ALLOWED_TRANSITIONS",
    "PROJECT_ID",
    "ProjectNotFoundError",
    "ProjectStateConflictError",
    "VideoProjectStore",
    "VideoProjectStoreError",
]
