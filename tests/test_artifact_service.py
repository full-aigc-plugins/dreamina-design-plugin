from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from scripts.artifact_service import (
    ArtifactPolicyError,
    ArtifactService,
    ArtifactTruncatedError,
    MediaMetadataMissingError,
)


def _png_bytes(width: int = 4, height: int = 4) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data)
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * width for _ in range(height))
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class ArtifactServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = ArtifactService(destination_root=self.root)

    def test_remote_fetch_is_disabled(self) -> None:
        with self.assertRaises(ArtifactPolicyError):
            self.service.download(
                submit_id="sub",
                url="https://example.test/file.png",
                destination=self.root / "file.png",
                expected_checksum={"algorithm": "sha256", "digest": "a" * 64},
            )

    def test_verify_local_cli_download_returns_receipt(self) -> None:
        path = self.root / "downloaded.png"
        path.write_bytes(_png_bytes())
        receipt = self.service.verify_local(submit_id="sub-local", path=path)
        self.assertEqual(receipt["submit_id"], "sub-local")
        self.assertEqual(receipt["media_metadata"]["mime_type"], "image/png")
        self.assertEqual((receipt["media_metadata"]["width"], receipt["media_metadata"]["height"]), (4, 4))
        self.assertEqual(len(receipt["checksum"]["digest"]), 64)

    def test_rejects_outside_root_symlink_unknown_and_truncated(self) -> None:
        outside = self.root.parent / "outside.png"
        outside.write_bytes(_png_bytes())
        with self.assertRaises(ArtifactPolicyError):
            self.service.verify_local(submit_id="sub", path=outside)
        link = self.root / "link.png"
        link.symlink_to(outside)
        with self.assertRaises(ArtifactPolicyError):
            self.service.verify_local(submit_id="sub", path=link)
        unknown = self.root / "unknown.bin"
        unknown.write_bytes(b"not media")
        with self.assertRaises(MediaMetadataMissingError):
            self.service.verify_local(submit_id="sub", path=unknown)
        truncated = self.root / "truncated.png"
        truncated.write_bytes(b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(ArtifactTruncatedError):
            self.service.verify_local(submit_id="sub", path=truncated)


if __name__ == "__main__":
    unittest.main()
