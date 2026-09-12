"""RED tests for the Dreamina artifact service (Task 5).

Validates downloaded artifacts before declaring a generation task
complete:

* byte-level checksum matches the manifest;
* media metadata (mime, dimensions, duration) is consistent;
* partial / truncated downloads are rejected;
* HTTP failures are surfaced, not swallowed.
"""

from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - exercised by RED phase
    from scripts.artifact_service import (  # type: ignore  # noqa: E402
        ArtifactChecksumMismatchError,
        ArtifactDownloadError,
        ArtifactService,
        ArtifactTruncatedError,
        MediaMetadataMissingError,
    )
except ModuleNotFoundError:  # pragma: no cover
    ArtifactService = None  # type: ignore[assignment]
    ArtifactChecksumMismatchError = None  # type: ignore[assignment]
    ArtifactDownloadError = None  # type: ignore[assignment]
    ArtifactTruncatedError = None  # type: ignore[assignment]
    MediaMetadataMissingError = None  # type: ignore[assignment]


def _png_bytes(width: int = 4, height: int = 4) -> bytes:
    """Tiny valid PNG with an RGBA payload."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data)
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b""
    for _ in range(height):
        raw += b"\x00" + (b"\x00\x00\x00\xff" * width)
    idat = zlib.compress(raw)
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class ModuleExportTests(unittest.TestCase):
    def test_module_exports_service(self) -> None:
        self.assertIsNotNone(ArtifactService)


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_download_writes_file_with_correct_checksum(self) -> None:
        payload = _png_bytes()
        digest = hashlib.sha256(payload).hexdigest()

        class _Fetcher:
            def fetch(self, url: str) -> bytes:
                return payload

        service = ArtifactService(fetcher=_Fetcher())
        receipt = service.download(
            submit_id="sub-1",
            url="https://example.test/image.png",
            destination=Path(self.tmp.name) / "image.png",
            expected_checksum={"algorithm": "sha256", "digest": digest},
        )
        self.assertEqual(receipt["submit_id"], "sub-1")
        self.assertEqual(receipt["checksum"]["digest"], digest)
        self.assertEqual(receipt["media_metadata"]["width"], 4)
        self.assertEqual(receipt["media_metadata"]["height"], 4)

    def test_download_failure_raises(self) -> None:
        class _Fetcher:
            def fetch(self, url: str) -> bytes:
                raise RuntimeError("network down")

        service = ArtifactService(fetcher=_Fetcher())
        with self.assertRaises(ArtifactDownloadError):
            service.download(
                submit_id="sub-1",
                url="https://example.test/image.png",
                destination=Path(self.tmp.name) / "image.png",
                expected_checksum={"algorithm": "sha256", "digest": "a" * 64},
            )

    def test_truncated_download_raises(self) -> None:
        payload = _png_bytes()
        truncated = payload[: len(payload) // 2]
        digest = hashlib.sha256(payload).hexdigest()

        class _Fetcher:
            def fetch(self, url: str) -> bytes:
                return truncated

        service = ArtifactService(fetcher=_Fetcher())
        with self.assertRaises(ArtifactTruncatedError):
            service.download(
                submit_id="sub-2",
                url="https://example.test/image.png",
                destination=Path(self.tmp.name) / "image.png",
                expected_checksum={"algorithm": "sha256", "digest": digest},
            )


class ChecksumTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_checksum_mismatch_raises(self) -> None:
        payload = _png_bytes()

        class _Fetcher:
            def fetch(self, url: str) -> bytes:
                return payload

        service = ArtifactService(fetcher=_Fetcher())
        with self.assertRaises(ArtifactChecksumMismatchError):
            service.download(
                submit_id="sub-3",
                url="https://example.test/image.png",
                destination=Path(self.tmp.name) / "image.png",
                expected_checksum={"algorithm": "sha256", "digest": "f" * 64},
            )


class MediaMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_unrecognized_mime_rejected(self) -> None:
        payload = b"NOT A VALID MEDIA FILE"

        class _Fetcher:
            def fetch(self, url: str) -> bytes:
                return payload

        service = ArtifactService(fetcher=_Fetcher())
        with self.assertRaises(MediaMetadataMissingError):
            service.download(
                submit_id="sub-4",
                url="https://example.test/binary.bin",
                destination=Path(self.tmp.name) / "binary.bin",
                expected_checksum={
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(payload).hexdigest(),
                },
            )


if __name__ == "__main__":
    unittest.main()
