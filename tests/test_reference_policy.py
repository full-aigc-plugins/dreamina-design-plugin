from __future__ import annotations

import tempfile
import os
import hashlib
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

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

    def _durable_policy_and_source(self):
        durable = Path(self.tmp.name) / "durable"
        source = self.root / "quote.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"q" * 64)
        policy = ReferencePolicy(approved_roots=[self.root], durable_root=durable)
        self.addCleanup(policy.close)
        return policy, source, durable

    def test_quote_reference_is_content_addressed_private_and_stable(self) -> None:
        policy, source, durable = self._durable_policy_and_source()
        result = policy.validate_for_quote({"path": str(source), "role": "subject"})
        target = Path(result["path"])
        self.assertEqual(target.parent, durable.resolve())
        self.assertEqual(target.stem, result["sha256"])
        self.assertEqual(target.stat().st_mode & 0o777, 0o400)
        self.assertNotIn("source_path", result)

    def test_source_replacement_during_publication_fails_closed(self) -> None:
        policy, source, _ = self._durable_policy_and_source()
        original_copy = policy._copy_stream

        def replace_after_copy(reader, writer):
            original_copy(reader, writer)
            replacement = source.with_suffix(".replacement")
            replacement.write_bytes(b"\x89PNG\r\n\x1a\n" + b"r" * 64)
            os.replace(replacement, source)

        with patch.object(policy, "_copy_stream", side_effect=replace_after_copy):
            with self.assertRaisesRegex(ReferencePolicyError, "changed"):
                policy.validate_for_quote({"path": str(source), "role": "subject"})

    def test_durable_root_swap_fails_closed(self) -> None:
        policy, source, durable = self._durable_policy_and_source()
        original_link = os.link

        def swap_before_link(*args, **kwargs):
            detached = durable.with_name("durable.detached")
            durable.rename(detached)
            durable.mkdir(mode=0o700)
            return original_link(*args, **kwargs)

        with patch("scripts.reference_policy.os.link", side_effect=swap_before_link):
            with self.assertRaisesRegex(ReferencePolicyError, "durable root changed"):
                policy.validate_for_quote({"path": str(source), "role": "subject"})

    def test_corrupt_existing_winner_is_never_overwritten(self) -> None:
        policy, source, durable = self._durable_policy_and_source()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        winner = durable / f"{digest}.png"
        winner.write_bytes(b"corrupt")
        winner.chmod(0o400)
        with self.assertRaisesRegex(ReferencePolicyError, "digest or size"):
            policy.validate_for_quote({"path": str(source), "role": "subject"})
        self.assertEqual(winner.read_bytes(), b"corrupt")

    def test_winner_replacement_during_validation_fails_closed(self) -> None:
        policy, source, durable = self._durable_policy_and_source()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        target = durable.resolve() / f"{digest}.png"
        original_digest = policy._sha256_fd

        def replace_after_digest(fd):
            result = original_digest(fd)
            if target.exists():
                target.unlink()
                target.write_bytes(source.read_bytes())
                target.chmod(0o400)
            return result

        with patch.object(policy, "_sha256_fd", side_effect=replace_after_digest):
            with self.assertRaisesRegex(ReferencePolicyError, "changed"):
                policy.validate_for_quote({"path": str(source), "role": "subject"})

    def test_concurrent_identical_publication_accepts_one_shared_winner(self) -> None:
        policy, source, _ = self._durable_policy_and_source()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: policy.validate_for_quote({"path": str(source), "role": "subject"}),
                range(2),
            ))
        self.assertEqual(results[0]["path"], results[1]["path"])
        self.assertEqual(results[0]["sha256"], results[1]["sha256"])


if __name__ == "__main__":
    unittest.main()
