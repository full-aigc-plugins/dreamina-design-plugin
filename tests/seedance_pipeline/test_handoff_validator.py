"""Tests for the Dreamina 3D handoff validator.

The validator MUST reject any preview receipt that:
  - is not produced by codex-blender or codex-maya,
  - is not on schema version 1.0.0,
  - has a producer version outside the supported range,
  - has restoration.status != 'confirmed',
  - has a path that is missing, or whose stat/hash no longer matches,
  - has unsupported media (codec/container/dimensions/fps/duration/bytes).

It MUST independently re-hash the on-disk file (the producer hash is not trusted
without verification) and MUST detect a file that is mutated while validation
runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "seedance_pipeline"))

from handoff_validator import (  # noqa: E402  -- path-injected import
    SUPPORTED_PRODUCERS,
    SUPPORTED_SCHEMA_VERSION,
    compatible_producer,
    validate_artifact,
)

FIXTURES = ROOT / "tests" / "seedance_pipeline" / "fixtures"


def _write_valid_receipt(**overrides):
    base = json.loads((FIXTURES / "blender_artifact.json").read_text())
    base.update(overrides)
    return base


def _write_real_mp4_like(tmp: Path, payload: bytes = b"\x00\x00\x00\x18ftypisom") -> Path:
    target = tmp / "preview.mp4"
    target.write_bytes(payload)
    return target


class HandoffValidatorTests(unittest.TestCase):
    def test_validate_artifact_accepts_compatible_blender_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = _write_real_mp4_like(tmp_path)
            sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            receipt = _write_valid_receipt(
                path=str(artifact),
                sha256=sha,
                bytes=artifact.stat().st_size,
            )
            errors = validate_artifact(receipt, artifact)
            self.assertEqual(errors, [])

    def test_validate_artifact_rejects_unknown_producer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = _write_real_mp4_like(tmp_path)
            sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            receipt = _write_valid_receipt(
                producer_plugin="vendor-uploader",
                path=str(artifact),
                sha256=sha,
                bytes=artifact.stat().st_size,
            )
            errors = validate_artifact(receipt, artifact)
            self.assertTrue(any("producer_plugin" in e for e in errors), errors)

    def test_validate_artifact_rejects_wrong_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = _write_real_mp4_like(tmp_path)
            sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            receipt = _write_valid_receipt(
                schema_version="0.9.0",
                path=str(artifact),
                sha256=sha,
                bytes=artifact.stat().st_size,
            )
            errors = validate_artifact(receipt, artifact)
            self.assertTrue(any("schema_version" in e for e in errors), errors)

    def test_validate_artifact_rejects_unconfirmed_restoration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = _write_real_mp4_like(tmp_path)
            sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            receipt = _write_valid_receipt(
                restoration={"status": "unknown"},
                path=str(artifact),
                sha256=sha,
                bytes=artifact.stat().st_size,
            )
            errors = validate_artifact(receipt, artifact)
            self.assertTrue(any("restoration" in e for e in errors), errors)

    def test_validate_artifact_rejects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = _write_real_mp4_like(tmp_path)
            receipt = _write_valid_receipt(
                path=str(artifact),
                sha256="f" * 64,
                bytes=artifact.stat().st_size,
            )
            errors = validate_artifact(receipt, artifact)
            self.assertTrue(any("sha256" in e for e in errors), errors)

    def test_validate_artifact_rejects_missing_file(self) -> None:
        receipt = _write_valid_receipt(path="/no/such/file.mp4")
        errors = validate_artifact(receipt, Path("/no/such/file.mp4"))
        self.assertTrue(any("path" in e or "missing" in e.lower() for e in errors), errors)

    def test_validate_artifact_rejects_unsupported_codec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = _write_real_mp4_like(tmp_path)
            sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            receipt = _write_valid_receipt(
                codec="prores",
                path=str(artifact),
                sha256=sha,
                bytes=artifact.stat().st_size,
            )
            errors = validate_artifact(receipt, artifact)
            self.assertTrue(any("codec" in e for e in errors), errors)

    def test_validate_artifact_rejects_oversized_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = _write_real_mp4_like(tmp_path)
            sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            receipt = _write_valid_receipt(
                duration_seconds=120.0,
                path=str(artifact),
                sha256=sha,
                bytes=artifact.stat().st_size,
            )
            errors = validate_artifact(receipt, artifact)
            self.assertTrue(any("duration" in e for e in errors), errors)

    def test_validate_artifact_detects_mutation_during_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = _write_real_mp4_like(tmp_path)
            sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            receipt = _write_valid_receipt(
                path=str(artifact),
                sha256=sha,
                bytes=artifact.stat().st_size,
            )

            stop = threading.Event()

            def mutator() -> None:
                while not stop.is_set():
                    try:
                        artifact.write_bytes(b"\x00\x00\x00\x18ftypisomMUTATED")
                    except OSError:
                        return

            t = threading.Thread(target=mutator, daemon=True)
            t.start()
            try:
                # Run several validations; at least one should observe mutation.
                observed_mutation = False
                for _ in range(20):
                    errors = validate_artifact(receipt, artifact)
                    if any("hash" in e or "size" in e or "mismatch" in e for e in errors):
                        observed_mutation = True
                        break
                    # Re-establish the matched hash between runs to give the
                    # mutator a chance to win the race.
                    receipt["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
                    receipt["bytes"] = artifact.stat().st_size
                self.assertTrue(observed_mutation, "validator did not detect concurrent mutation")
            finally:
                stop.set()
                t.join(timeout=1.0)

    def test_compatible_producer_accepts_supported_versions(self) -> None:
        self.assertTrue(compatible_producer("codex-blender", "0.1.0", {"codex-blender": [("0.1.0", "0.1.99")]}))

    def test_compatible_producer_rejects_too_old_version(self) -> None:
        self.assertFalse(compatible_producer("codex-blender", "0.0.1", {"codex-blender": [("0.1.0", "0.1.99")]}))

    def test_supported_constants_are_pinned(self) -> None:
        self.assertEqual(SUPPORTED_SCHEMA_VERSION, "1.0.0")
        self.assertEqual(SUPPORTED_PRODUCERS, ("codex-blender", "codex-maya"))


if __name__ == "__main__":
    unittest.main()