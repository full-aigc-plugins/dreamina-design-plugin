"""Artifact download + verification for Dreamina Design (Task 5).

Validates downloaded artifacts before declaring a generation task
complete. The service refuses to return an ``ArtifactReceipt`` unless
all of the following hold:

* the fetch completed without raising;
* the payload is non-empty and well-formed;
* the SHA-256 digest of the bytes matches the expected digest;
* media metadata can be derived for known formats (PNG, JPEG, MP4 …).
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class ArtifactServiceError(Exception):
    """Base class for artifact service errors."""


class ArtifactDownloadError(ArtifactServiceError):
    """The underlying fetcher failed to download the artifact."""


class ArtifactTruncatedError(ArtifactServiceError):
    """The downloaded payload is empty or smaller than a known minimum."""


class ArtifactChecksumMismatchError(ArtifactServiceError):
    """The computed SHA-256 does not match the expected digest."""


class MediaMetadataMissingError(ArtifactServiceError):
    """No media metadata could be derived from the downloaded payload."""


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
MP4_FTYP = b"ftyp"

MIN_PNG_BYTES = len(PNG_SIGNATURE) + 25  # signature + IHDR chunk (length+type+13-byte payload+CRC)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _parse_png_dimensions(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < 24 or payload[:8] != PNG_SIGNATURE:
        return None
    # IHDR chunk: 4-byte length, 4-byte type, 13-byte payload, 4-byte CRC
    # Total chunk header is bytes 8..12 (length) + bytes 12..16 (type).
    if payload[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", payload[16:24])
    if width <= 0 or height <= 0:
        return None
    return width, height


def _detect_mime(payload: bytes) -> str | None:
    if payload.startswith(PNG_SIGNATURE):
        return "image/png"
    if payload.startswith(JPEG_SIGNATURE):
        return "image/jpeg"
    if len(payload) >= 12 and payload[4:8] == MP4_FTYP:
        return "video/mp4"
    return None


def _looks_complete(payload: bytes, mime: str) -> bool:
    """Quick check that the payload contains a known terminator."""
    if mime == "image/png":
        return b"IEND" in payload[-12:]
    if mime == "image/jpeg":
        # JPEG ends with the EOI marker (FFD9).
        return payload[-2:] == b"\xff\xd9"
    if mime == "video/mp4":
        return b"moov" in payload[-1024:]
    return True


def _media_metadata(payload: bytes, mime: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"mime_type": mime, "size_bytes": len(payload)}
    if mime == "image/png":
        dims = _parse_png_dimensions(payload)
        if dims is None:
            return metadata
        metadata["width"], metadata["height"] = dims
    return metadata


class ArtifactService:
    """Download an artifact, validate it, and return an ArtifactReceipt."""

    def __init__(self, *, fetcher: Any | None = None, min_bytes: int = MIN_PNG_BYTES) -> None:
        self._fetcher = fetcher
        self._min_bytes = min_bytes

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    def download(
        self,
        *,
        submit_id: str,
        url: str,
        destination: Path,
        expected_checksum: Mapping[str, str],
    ) -> dict[str, Any]:
        if not self._fetcher:
            raise ArtifactDownloadError("no fetcher configured")
        try:
            payload = self._fetcher.fetch(url)
        except Exception as exc:  # noqa: BLE001 — surface as typed error
            raise ArtifactDownloadError(f"download failed: {exc}") from exc
        if not isinstance(payload, (bytes, bytearray)) or len(payload) == 0:
            raise ArtifactTruncatedError("downloaded payload is empty")
        # Truncation check: if the payload *looks like* a known media format
        # (PNG / JPEG / MP4) but is too short to be a valid file, report
        # truncation rather than checksum mismatch.
        mime = _detect_mime(bytes(payload))
        if mime is not None and (
            len(payload) < self._min_bytes
            or not _looks_complete(bytes(payload), mime)
        ):
            raise ArtifactTruncatedError(
                f"downloaded payload looks like {mime} but appears truncated"
            )
        if mime is None:
            raise MediaMetadataMissingError(
                f"could not derive media metadata from payload (size {len(payload)})"
            )
        algorithm = expected_checksum.get("algorithm", "sha256").lower()
        digest = expected_checksum.get("digest", "").lower()
        if algorithm != "sha256" or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ArtifactChecksumMismatchError(
                f"unsupported checksum spec: algorithm={algorithm}"
            )
        actual = _sha256_hex(bytes(payload))
        if actual != digest:
            raise ArtifactChecksumMismatchError(
                f"checksum mismatch: expected {digest}, got {actual}"
            )
        metadata = _media_metadata(bytes(payload), mime)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(destination, bytes(payload))
        receipt = {
            "submit_id": submit_id,
            "local_path": str(destination),
            "checksum": {"algorithm": algorithm, "digest": actual},
            "media_metadata": metadata,
            "verified_at": _now_iso(),
        }
        return receipt

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".part")
        try:
            with open(tmp_path, "wb") as handle:
                handle.write(payload)
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise


__all__ = [
    "ArtifactChecksumMismatchError",
    "ArtifactDownloadError",
    "ArtifactService",
    "ArtifactTruncatedError",
    "MediaMetadataMissingError",
]
