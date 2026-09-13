"""Enrollment and private staging for trusted local media executables."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scripts.native_approval import NativeApprovalProvider


MEDIA_TOOL_KINDS = frozenset({"ffmpeg", "ffprobe", "whisper", "narration"})
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


class TrustedMediaToolError(PermissionError):
    """A media executable or its protected trust record is invalid."""


@dataclass(frozen=True)
class TrustedMediaTool:
    """A verified executable copied into a private per-process directory."""

    kind: str
    source_path: str
    sha256: str
    staged_path: str


class TrustedMediaToolStore:
    """Persist approved tool digests and create verified execution copies."""

    def __init__(self, path: Path | None = None, staging_root: Path | None = None) -> None:
        self.path = path or (
            Path.home() / ".config" / "codex-dreamina-design" / "trusted-media-tools.json"
        )
        self._staging_root = Path(staging_root) if staging_root is not None else None

    def enroll(self, kind: str, path: Path, approval_provider=None) -> dict[str, str]:
        """Approve and persist the identity of one fixed-purpose media tool."""
        self._validate_kinds({kind})
        source = Path(path)
        canonical, owner_uid, digest = self._inspect_source(source)
        provider = approval_provider or NativeApprovalProvider()
        provider.confirm_media_tool_enrollment(
            kind=kind,
            path=canonical,
            owner_uid=owner_uid,
            sha256=digest,
        )
        tools = self._load_config(allow_missing=True)
        record = {"source_path": canonical, "sha256": digest}
        tools[kind] = record
        self._atomic_write({"version": 1, "tools": tools})
        return record.copy()

    def load_required(self, kinds: Iterable[str]) -> dict[str, TrustedMediaTool]:
        """Load, re-hash, and privately stage every requested enrolled tool."""
        required = set(kinds)
        self._validate_kinds(required)
        tools = self._load_config()
        missing = required.difference(tools)
        if missing:
            raise TrustedMediaToolError(
                "required media tools are not enrolled: " + ", ".join(sorted(missing))
            )
        loaded: dict[str, TrustedMediaTool] = {}
        try:
            for kind in sorted(required):
                record = tools[kind]
                loaded[kind] = self._stage(kind, record["source_path"], record["sha256"])
            return loaded
        except Exception:
            for tool in loaded.values():
                self.release(tool)
            raise

    @staticmethod
    def release(tool: TrustedMediaTool) -> None:
        """Remove a private staged executable after its process lifetime."""
        shutil.rmtree(Path(tool.staged_path).parent, ignore_errors=True)

    @staticmethod
    def _validate_kinds(kinds: set[str]) -> None:
        if not kinds.issubset(MEDIA_TOOL_KINDS):
            unknown = kinds.difference(MEDIA_TOOL_KINDS)
            raise TrustedMediaToolError("unsupported media tool kind: " + ", ".join(sorted(unknown)))

    @staticmethod
    def _inspect_source(source: Path) -> tuple[str, int, str]:
        if not source.is_absolute() or source.is_symlink():
            raise TrustedMediaToolError(
                "media tool enrollment requires an absolute regular non-symlink file"
            )
        try:
            source_stat = source.stat()
        except OSError as exc:
            raise TrustedMediaToolError("media tool does not exist") from exc
        if not stat.S_ISREG(source_stat.st_mode) or not source_stat.st_mode & 0o111:
            raise TrustedMediaToolError("media tool must be a regular executable file")
        if source_stat.st_uid != os.getuid() or source_stat.st_mode & 0o022:
            raise TrustedMediaToolError("media tool owner or write permissions are not trusted")
        canonical = source.resolve(strict=True)
        for parent in canonical.parents:
            if parent.stat().st_mode & 0o022:
                raise TrustedMediaToolError(f"group/world-writable media tool parent: {parent}")
        digest, opened_stat = _digest_opened_file(canonical)
        if (opened_stat.st_dev, opened_stat.st_ino) != (source_stat.st_dev, source_stat.st_ino):
            raise TrustedMediaToolError("media tool changed during enrollment")
        return str(canonical), opened_stat.st_uid, digest

    def _load_config(self, *, allow_missing: bool = False) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            if allow_missing:
                return {}
            raise TrustedMediaToolError("trusted media tools are not enrolled")
        if self.path.is_symlink() or not self.path.is_file():
            raise TrustedMediaToolError("trusted media tool config must be a regular file")
        config_stat = self.path.stat()
        if config_stat.st_uid != os.getuid() or config_stat.st_mode & 0o077:
            raise TrustedMediaToolError("trusted media tool config must be user-owned mode 0600")
        parent = self.path.parent
        if parent.is_symlink() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
            raise TrustedMediaToolError(
                "trusted media tool config directory must be user-owned mode 0700"
            )
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrustedMediaToolError("trusted media tool config is invalid") from exc
        if not isinstance(payload, dict) or set(payload) != {"version", "tools"}:
            raise TrustedMediaToolError("trusted media tool config has unexpected fields")
        if payload["version"] != 1 or not isinstance(payload["tools"], dict):
            raise TrustedMediaToolError("trusted media tool config version is invalid")
        tools: dict[str, dict[str, str]] = {}
        for kind, record in payload["tools"].items():
            self._validate_kinds({kind})
            if not isinstance(record, dict) or set(record) != {"source_path", "sha256"}:
                raise TrustedMediaToolError("trusted media tool record has unexpected fields")
            source_path = record["source_path"]
            digest = record["sha256"]
            if not isinstance(source_path, str) or not Path(source_path).is_absolute():
                raise TrustedMediaToolError("trusted media tool source path is invalid")
            if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                raise TrustedMediaToolError("trusted media tool digest is invalid")
            tools[kind] = {"source_path": source_path, "sha256": digest}
        return tools

    def _stage(self, kind: str, source_path: str, expected_sha256: str) -> TrustedMediaTool:
        source = Path(source_path)
        canonical, _owner_uid, current_sha256 = self._inspect_source(source)
        if canonical != source_path or not hmac.compare_digest(current_sha256, expected_sha256):
            raise TrustedMediaToolError(f"trusted {kind} digest or path changed after enrollment")
        if self._staging_root is not None:
            self._staging_root.mkdir(parents=True, exist_ok=True)
            os.chmod(self._staging_root, 0o700)
            private_root = Path(tempfile.mkdtemp(prefix=f"{kind}-", dir=self._staging_root))
        else:
            private_root = Path(tempfile.mkdtemp(prefix=f"dreamina-{kind}-"))
        os.chmod(private_root, 0o700)
        staged = private_root / kind
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            digest = hashlib.sha256()
            with os.fdopen(os.open(source, flags), "rb") as reader, staged.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
                raise TrustedMediaToolError(f"trusted {kind} changed while being staged")
            os.chmod(staged, 0o500)
            return TrustedMediaTool(kind, source_path, expected_sha256, str(staged))
        except Exception:
            shutil.rmtree(private_root, ignore_errors=True)
            raise

    def _atomic_write(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        fd, tmp_name = tempfile.mkstemp(prefix=".trusted-media-tools.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise


def _digest_opened_file(path: Path) -> tuple[str, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TrustedMediaToolError("media tool could not be opened safely") from exc
    with os.fdopen(descriptor, "rb") as handle:
        opened_stat = os.fstat(handle.fileno())
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest(), opened_stat


__all__ = [
    "MEDIA_TOOL_KINDS",
    "TrustedMediaTool",
    "TrustedMediaToolError",
    "TrustedMediaToolStore",
]
