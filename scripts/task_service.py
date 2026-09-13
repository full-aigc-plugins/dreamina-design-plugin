"""Closed, query-only Dreamina task automation and verified downloads."""
from __future__ import annotations
import hashlib
import os
import re
import stat
from pathlib import Path
from collections.abc import Mapping
from scripts.output_redactor import redact_value

IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
FILTERS = {"gen_status", "aigc_type", "session"}
STATUS_MAP = {"querying": "querying", "queued": "querying", "pending": "querying",
              "processing": "querying", "running": "querying", "generating": "querying",
              "success": "success", "succeeded": "success", "completed": "success",
              "fail": "failed", "failed": "failed", "failure": "failed", "error": "failed"}
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024

class TaskService:
    def __init__(self, adapter): self.adapter = adapter
    def query(self, submit_id: str, *, poll_seconds: int = 0, download_dir: str | None = None) -> dict[str, object]:
        if not IDENTIFIER.fullmatch(str(submit_id)): raise ValueError("unsafe submit_id")
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, int) or not 0 <= poll_seconds <= 300: raise ValueError("poll_seconds must be between 0 and 300")
        argv = ["query_result", "--submit_id", submit_id]
        if poll_seconds: argv += ["--poll", str(poll_seconds)]
        directory_fd = None
        before: set[str] = set()
        if download_dir:
            target = Path(download_dir)
            if not target.is_absolute() or target.is_symlink(): raise ValueError("download directory must be absolute and non-symlink")
            metadata = target.stat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700: raise ValueError("download directory must be user-owned mode 0700")
            directory_fd = os.open(target, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
            before = set(os.listdir(directory_fd)); argv += ["--download_dir", str(target)]
        try:
            result = self.adapter.run(argv)
            if isinstance(result.exit_code, bool) or not isinstance(result.exit_code, int) or result.exit_code != 0: raise ValueError("query_result returned nonzero exit code")
            payload = redact_value(result.payload if isinstance(result.payload, Mapping) else {})
            raw_status = payload.get("gen_status") if isinstance(payload, Mapping) else None
            status_value = STATUS_MAP.get(str(raw_status).strip().lower()) if isinstance(raw_status, str) else None
            if status_value is None: raise ValueError("query_result returned unknown external status")
            payload = dict(payload); payload["gen_status"] = status_value
            artifacts = self._verified_artifacts(directory_fd, before, Path(download_dir)) if directory_fd is not None else []
            return {"submit_id": submit_id, "provenance": "externally-queried", "exit_code": 0,
                    "status": status_value, "result": payload, "artifacts": artifacts}
        finally:
            if directory_fd is not None: os.close(directory_fd)

    @staticmethod
    def _verified_artifacts(directory_fd: int, before: set[str], target: Path) -> list[dict[str, object]]:
        artifacts = []
        for name in sorted(set(os.listdir(directory_fd)) - before):
            if "/" in name or name in {".", ".."}: raise ValueError("unsafe downloaded artifact name")
            descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1: raise ValueError("downloaded artifact must be a private regular file")
                digest = hashlib.sha256(); prefix = b""; size = 0
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk: break
                    prefix += chunk[:max(0, 16-len(prefix))]; size += len(chunk)
                    if size > MAX_ARTIFACT_BYTES: raise ValueError("downloaded artifact size is invalid")
                    digest.update(chunk)
                if size == 0: raise ValueError("downloaded artifact size is invalid")
                mime = "image/png" if prefix.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if prefix.startswith(b"\xff\xd8\xff") else "video/mp4" if len(prefix) >= 12 and prefix[4:8] == b"ftyp" else None
                if mime is None: raise ValueError("downloaded artifact type is unsupported")
                os.fchmod(descriptor, 0o400)
                artifacts.append({"path": str((target / name).resolve()), "mime_type": mime, "size_bytes": size,
                                  "sha256": digest.hexdigest(), "provenance": "externally-queried"})
            finally: os.close(descriptor)
        return artifacts

    def list_tasks(self, filters: Mapping[str, object], *, limit: int = 20) -> dict[str, object]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100: raise ValueError("limit must be between 1 and 100")
        unknown = set(filters) - FILTERS
        if unknown: raise ValueError(f"unsupported task filters: {sorted(unknown)}")
        argv = ["list_task"]
        for key in ("gen_status", "aigc_type", "session"):
            if key in filters: argv += [f"--{key}", str(filters[key])]
        argv += ["--limit", str(limit)]; result = self.adapter.run(argv)
        if result.exit_code != 0: raise ValueError("list_task returned nonzero exit code")
        return {"exit_code": result.exit_code, "result": redact_value(result.payload if isinstance(result.payload, Mapping) else {})}
