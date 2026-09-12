"""Containment, type, and size policy for local Dreamina upload inputs."""

from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import tempfile
import uuid
import weakref
from pathlib import Path
from typing import Any, Iterable, Mapping


class ReferencePolicyError(ValueError):
    """A local upload reference violates the explicit approval boundary."""


IMAGE_ROLES = {"subject", "style", "frame"}
DEFAULT_MAX_IMAGE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_MEDIA_BYTES = 512 * 1024 * 1024


def _detect_mime(header: bytes) -> str | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"RIFF",)) and header[8:12] == b"WEBP":
        return "image/webp"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4"
    if header.startswith(b"ID3") or header.startswith((b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
        return "audio/mpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "audio/wav"
    return None


class ReferencePolicy:
    """Validate upload files against user-approved filesystem roots."""

    def __init__(
        self,
        *,
        approved_roots: Iterable[Path],
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
        max_media_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
    ) -> None:
        self._roots = tuple(Path(root).resolve(strict=True) for root in approved_roots)
        if not self._roots or any(not root.is_dir() for root in self._roots):
            raise ValueError("approved_roots must contain existing directories")
        if max_image_bytes <= 0 or max_media_bytes <= 0:
            raise ValueError("reference size limits must be positive")
        self._max_image_bytes = max_image_bytes
        self._max_media_bytes = max_media_bytes
        self._staging_root = Path(tempfile.mkdtemp(prefix="dreamina-references-"))
        os.chmod(self._staging_root, 0o700)
        atexit.register(shutil.rmtree, self._staging_root, True)
        self._finalizer = weakref.finalize(self, shutil.rmtree, self._staging_root, True)

    def validate(self, reference: Mapping[str, Any]) -> dict[str, Any]:
        raw_path = Path(str(reference.get("path", "")))
        if not raw_path.is_absolute() or raw_path.is_symlink():
            raise ReferencePolicyError("reference path must be an absolute non-symlink path")
        try:
            resolved = raw_path.resolve(strict=True)
        except OSError as exc:
            raise ReferencePolicyError(f"reference file is unavailable: {raw_path}") from exc
        if not resolved.is_file():
            raise ReferencePolicyError("reference must be a regular file without symlink traversal")
        if not any(_is_within(resolved, root) for root in self._roots):
            raise ReferencePolicyError("reference escapes approved roots")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(raw_path, flags)
        opened_stat = os.fstat(fd)
        current_stat = resolved.stat()
        if (opened_stat.st_dev, opened_stat.st_ino) != (current_stat.st_dev, current_stat.st_ino):
            os.close(fd)
            raise ReferencePolicyError("reference changed while it was being opened")
        size = opened_stat.st_size
        declared_size = reference.get("size_bytes")
        if declared_size is not None and int(declared_size) != size:
            raise ReferencePolicyError("reference size_bytes does not match the file")
        staged = self._staging_root / f"{uuid.uuid4().hex}{resolved.suffix.lower()}"
        digest = hashlib.sha256()
        header = b""
        with os.fdopen(fd, "rb") as reader, staged.open("xb") as writer:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                if not header:
                    header = chunk[:32]
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        os.chmod(staged, 0o400)
        mime = _detect_mime(header)
        if mime is None:
            raise ReferencePolicyError("reference media type is not recognized")
        role = str(reference.get("role", "")).lower()
        if role in IMAGE_ROLES and not mime.startswith("image/"):
            raise ReferencePolicyError(f"role {role} requires an image")
        if role == "audio" and not mime.startswith("audio/"):
            raise ReferencePolicyError("audio role requires an audio file")
        if role == "reference" and not mime.startswith("video/"):
            raise ReferencePolicyError("reference role requires a video file")
        limit = self._max_image_bytes if mime.startswith("image/") else self._max_media_bytes
        if size > limit:
            raise ReferencePolicyError(f"reference exceeds byte limit {limit}")
        normalized = dict(reference)
        normalized["source_path"] = str(resolved)
        normalized["path"] = str(staged)
        normalized["mime_type"] = mime
        normalized["size_bytes"] = size
        normalized["sha256"] = digest.hexdigest()
        return normalized

    def close(self) -> None:
        self._finalizer()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = ["ReferencePolicy", "ReferencePolicyError"]
