"""Bind a live Dreamina capability snapshot to the enrolled CLI inode."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from scripts.dreamina_adapter import DreaminaAdapter
from scripts.json_contracts import canonical_fingerprint, validate_contract
from scripts.trusted_cli import TrustedCliError, TrustedCliStore


class TrustedCapabilityProvider:
    """Capture read-only capabilities from the exact enrolled CLI bytes."""

    def __init__(self, trusted_cli: TrustedCliStore) -> None:
        self._trusted_cli = trusted_cli

    def capture(self) -> dict[str, Any]:
        enrollment = self._trusted_cli.load()
        path = Path(enrollment["cli_path"])
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise TrustedCliError("enrolled CLI cannot be opened safely") from exc
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid not in {0, os.getuid()} or metadata.st_mode & 0o022:
                raise TrustedCliError("enrolled CLI identity is unsafe")
            digest = hashlib.sha256()
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
            sha256 = digest.hexdigest()
            if sha256 != enrollment["cli_sha256"]:
                raise TrustedCliError("enrolled CLI digest changed")
            path_metadata = os.stat(path, follow_symlinks=False)
            if (path_metadata.st_dev, path_metadata.st_ino, path_metadata.st_size) != (metadata.st_dev, metadata.st_ino, metadata.st_size):
                raise TrustedCliError("enrolled CLI path changed during capture")
        finally:
            os.close(fd)
        adapter = DreaminaAdapter(cli_command=str(path), trusted_binary_sha256=sha256)
        snapshot = adapter.capability_snapshot()
        validate_contract(snapshot, "capability_snapshot.schema.json")
        return {
            "snapshot": snapshot,
            "identity_receipt": {
                "cli_path": str(path.resolve(strict=True)), "cli_sha256": sha256,
                "device": metadata.st_dev, "inode": metadata.st_ino, "size_bytes": metadata.st_size,
                "mode": stat.S_IMODE(metadata.st_mode), "owner_uid": metadata.st_uid,
                "cli_version": snapshot["cli_version"], "cli_commit": snapshot.get("cli_commit"),
                "captured_at": snapshot["captured_at"],
                "snapshot_fingerprint": canonical_fingerprint(snapshot),
            },
        }


__all__ = ["TrustedCapabilityProvider"]
