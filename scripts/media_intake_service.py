"""Fail-closed local source-video intake into private project storage."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from scripts.json_contracts import canonical_fingerprint, validate_contract
from scripts.native_approval import NativeApprovalProvider
from scripts.video_project_store import VideoProjectStore


DEFAULT_MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_DURATION_SECONDS = 1800.0


class MediaIntakeError(RuntimeError):
    """The source failed a local trust, type, or stability check."""


class MediaLimitError(MediaIntakeError):
    """The source must be segmented before it can be processed."""

    def __init__(self, reason: str) -> None:
        self.action = {"type": "segment_source", "reason": reason}
        super().__init__(f"segment_source: {reason}")


class MediaIntakeService:
    """Copy an approved source into one project after native confirmation."""

    def __init__(
        self,
        project_store: VideoProjectStore,
        media_adapter,
        approval_provider: NativeApprovalProvider | None = None,
        *,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
        max_duration_seconds: float = DEFAULT_MAX_DURATION_SECONDS,
    ) -> None:
        self._store = project_store
        self._media_adapter = media_adapter
        self._approval_provider = approval_provider or NativeApprovalProvider()
        self._max_source_bytes = max_source_bytes
        self._max_duration_seconds = max_duration_seconds

    def intake(
        self,
        project_id: str,
        source_path: Path,
        approved_roots: Iterable[Path],
        *,
        probed_duration_seconds: float | None = None,
    ) -> dict[str, Any]:
        source = Path(source_path)
        if not source.is_absolute():
            raise MediaIntakeError("source path must be absolute")
        roots = self._approved_roots(approved_roots)
        self._require_inside(source, roots)
        try:
            before_path = source.lstat()
        except OSError as exc:
            raise MediaIntakeError("source cannot be inspected") from exc
        if stat.S_ISLNK(before_path.st_mode) or not stat.S_ISREG(before_path.st_mode):
            raise MediaIntakeError("source must be a regular non-symlink file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(source, flags)
        except OSError as exc:
            raise MediaIntakeError("source cannot be opened without following links") from exc
        staged_path: Path | None = None
        staged_created = False
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before_path.st_dev, before_path.st_ino):
                raise MediaIntakeError("source inode changed during open")
            if opened.st_size > self._max_source_bytes:
                raise MediaLimitError("source exceeds 2 GiB")
            mime_type = self._mime_type(descriptor)
            staged_path, digest, staged_created = self._stage(
                project_id, source.suffix.lower(), descriptor
            )
            probe = self._media_adapter.probe_json(staged_path)
            after_path = source.lstat()
            if (after_path.st_dev, after_path.st_ino) != (opened.st_dev, opened.st_ino):
                raise MediaIntakeError("source inode changed during intake")
            metadata = self._probe_metadata(probe)
            duration = (
                float(probed_duration_seconds)
                if probed_duration_seconds is not None
                else metadata["duration_seconds"]
            )
            if duration > self._max_duration_seconds:
                raise MediaLimitError("source exceeds 1800 seconds")
            receipt = {
                "schema_version": "1.0",
                "project_id": project_id,
                "source_sha256": digest,
                "size_bytes": opened.st_size,
                "mime_type": mime_type,
                "video_codec": metadata["video_codec"],
                "width": metadata["width"],
                "height": metadata["height"],
                "fps": metadata["fps"],
                "duration_seconds": duration,
                "audio_streams": metadata["audio_streams"],
                "approved_roots_digest": canonical_fingerprint(
                    {"approved_roots": [str(root) for root in roots]}
                ),
                "staged_path": str(staged_path),
                "intake_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            validate_contract(receipt, "source_receipt.schema.json")
            self._approval_provider.confirm(
                {
                    "operation": "reference-video-source-intake",
                    "project_id": project_id,
                    "source_sha256": digest,
                    "purpose": "reference-video-processing",
                }
            )
            self._store.write_version(project_id, "source_receipt", receipt)
            return receipt
        except BaseException:
            if staged_path is not None and staged_created:
                try:
                    staged_path.chmod(0o600)
                    staged_path.unlink()
                except FileNotFoundError:
                    pass
            raise
        finally:
            os.close(descriptor)

    @staticmethod
    def _approved_roots(values: Iterable[Path]) -> tuple[Path, ...]:
        roots: list[Path] = []
        for value in values:
            root = Path(value)
            if not root.is_absolute() or root.is_symlink():
                raise MediaIntakeError("approved roots must be absolute non-symlink directories")
            try:
                resolved = root.resolve(strict=True)
            except OSError as exc:
                raise MediaIntakeError("approved root does not exist") from exc
            if not resolved.is_dir():
                raise MediaIntakeError("approved root must be a directory")
            roots.append(resolved)
        if not roots:
            raise MediaIntakeError("at least one approved root is required")
        return tuple(sorted(set(roots), key=str))

    @staticmethod
    def _require_inside(source: Path, roots: tuple[Path, ...]) -> None:
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise MediaIntakeError("source does not exist") from exc
        if source.is_symlink() or not any(resolved.is_relative_to(root) for root in roots):
            raise MediaIntakeError("source is outside approved roots or is a symlink")

    def _stage(
        self, project_id: str, suffix: str, source_descriptor: int
    ) -> tuple[Path, str, bool]:
        source_root = self._store.project_root(project_id) / "source"
        source_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(source_root, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".intake-", dir=source_root)
        digest = hashlib.sha256()
        try:
            os.fchmod(descriptor, 0o600)
            os.lseek(source_descriptor, 0, os.SEEK_SET)
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    chunk = os.read(source_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            final = source_root / f"{digest.hexdigest()}{suffix or '.video'}"
            try:
                os.link(temporary, final, follow_symlinks=False)
                created = True
            except FileExistsError:
                created = False
            os.unlink(temporary)
            os.chmod(final, 0o400)
            directory = os.open(source_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return final, digest.hexdigest(), created
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

    @staticmethod
    def _mime_type(descriptor: int) -> str:
        os.lseek(descriptor, 0, os.SEEK_SET)
        header = os.read(descriptor, 16)
        if len(header) >= 12 and header[4:8] == b"ftyp":
            brand = header[8:12]
            return "video/quicktime" if brand == b"qt  " else "video/mp4"
        if header.startswith(b"\x1aE\xdf\xa3"):
            return "video/x-matroska"
        raise MediaIntakeError("source bytes are not a supported video container")

    @staticmethod
    def _probe_metadata(probe: dict[str, object]) -> dict[str, Any]:
        streams = probe.get("streams")
        file_format = probe.get("format")
        if not isinstance(streams, list) or not isinstance(file_format, dict):
            raise MediaIntakeError("ffprobe returned incomplete metadata")
        videos = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"]
        if not videos:
            raise MediaIntakeError("ffprobe found no video stream")
        video = videos[0]
        try:
            numerator, denominator = str(video.get("avg_frame_rate", "0/1")).split("/", 1)
            fps = float(numerator) / float(denominator)
            duration = float(file_format["duration"])
            result = {
                "video_codec": str(video["codec_name"]),
                "width": int(video["width"]),
                "height": int(video["height"]),
                "fps": fps,
                "duration_seconds": duration,
                "audio_streams": [
                    {
                        "codec": str(item["codec_name"]),
                        "channels": int(item.get("channels", 0)),
                        "sample_rate": int(item.get("sample_rate", 0)),
                    }
                    for item in streams
                    if isinstance(item, dict) and item.get("codec_type") == "audio"
                ],
            }
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise MediaIntakeError("ffprobe returned invalid metadata") from exc
        if result["width"] < 1 or result["height"] < 1 or fps <= 0 or duration < 0:
            raise MediaIntakeError("ffprobe metadata is outside valid bounds")
        return result


__all__ = [
    "DEFAULT_MAX_DURATION_SECONDS",
    "DEFAULT_MAX_SOURCE_BYTES",
    "MediaIntakeError",
    "MediaIntakeService",
    "MediaLimitError",
]
