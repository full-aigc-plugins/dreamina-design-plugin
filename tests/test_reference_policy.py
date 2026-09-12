from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.reference_policy import ReferencePolicy, ReferencePolicyError


class ReferencePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "approved"
        self.root.mkdir()
        self.policy = ReferencePolicy(approved_roots=[self.root])

    def test_accepts_regular_png_inside_approved_root(self) -> None:
        path = self.root / "input.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
        result = self.policy.validate({"path": str(path), "role": "subject"})
        self.assertEqual(result["mime_type"], "image/png")
        self.assertEqual(result["size_bytes"], path.stat().st_size)
        self.assertEqual(len(result["sha256"]), 64)
        staged = Path(result["path"])
        original_bytes = staged.read_bytes()
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 64)
        self.assertEqual(staged.read_bytes(), original_bytes)
        self.assertNotEqual(result["path"], result["source_path"])

    def test_rejects_outside_root_symlink_and_type_mismatch(self) -> None:
        outside = Path(self.tmp.name) / "outside.png"
        outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
        with self.assertRaises(ReferencePolicyError):
            self.policy.validate({"path": str(outside), "role": "subject"})
        link = self.root / "link.png"
        link.symlink_to(outside)
        with self.assertRaises(ReferencePolicyError):
            self.policy.validate({"path": str(link), "role": "subject"})
        audio = self.root / "audio.mp3"
        audio.write_bytes(b"ID3" + b"x" * 64)
        with self.assertRaises(ReferencePolicyError):
            self.policy.validate({"path": str(audio), "role": "frame"})

    def test_rejects_declared_size_mismatch_and_oversize(self) -> None:
        path = self.root / "input.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
        with self.assertRaises(ReferencePolicyError):
            self.policy.validate({"path": str(path), "role": "subject", "size_bytes": 1})
        policy = ReferencePolicy(approved_roots=[self.root], max_image_bytes=16)
        with self.assertRaises(ReferencePolicyError):
            policy.validate({"path": str(path), "role": "subject"})


if __name__ == "__main__":
    unittest.main()
