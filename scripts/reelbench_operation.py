"""Sealed, descriptor-owned operation markers and conservative crash recovery."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat

from scripts import reelbench_workspace as ws
from scripts.json_contracts import canonical_fingerprint
from scripts.video_project_store import VersionCommitIndeterminateError

MARKER = ".reelbench-operation.json"
KEY = ".reelbench-operation-key"
_NAME = re.compile(r"\.reelbench-work-[a-f0-9]{24}")
_VERSION = re.compile(r"v[0-9]{3,}")


class ReelBenchRecoveryRequiredError(ValueError):
    """An unknown, live, or unsealed operation requires manual recovery."""


def _identity(fd):
    info = os.fstat(fd)
    return {"device": info.st_dev, "inode": info.st_ino}


class OperationJournal:
    def __init__(self, store_fd, project_fd, project_id, project_path):
        self.project_fd = project_fd
        self.project_id = project_id
        self.project_path = project_path
        try:
            key = ws.read(store_fd, KEY, 32)
        except FileNotFoundError:
            ws.write(store_fd, KEY, secrets.token_bytes(32))
            key = ws.read(store_fd, KEY, 32)
        with ws.file_at(store_fd, KEY) as fd:
            info = os.fstat(fd)
            if len(key) != 32 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ReelBenchRecoveryRequiredError("operation seal key is not private")
        self.key = key

    def create(self, work, name, *, action, version, parent, parent_fingerprint, source):
        fcntl.flock(work, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = {"marker_version": "1", "operation": name, "project_id": self.project_id,
            "project_identity": _identity(self.project_fd), "workspace_identity": _identity(work),
            "action": action, "version": version, "parent_version": parent,
            "parent_fingerprint": parent_fingerprint, "source_receipt_version": source["version"],
            "source_fingerprint": canonical_fingerprint(source), "output_identity": None,
            "receipt_fingerprint": None}
        self.write(work, marker)
        return marker

    def write(self, work, marker):
        body = {key: value for key, value in marker.items() if key != "seal"}
        sealed = {**body, "seal": hmac.new(self.key, canonical_fingerprint(body).encode(), hashlib.sha256).hexdigest()}
        temporary = ".operation-tmp-" + secrets.token_hex(12)
        try:
            ws.write(work, temporary, json.dumps(sealed, sort_keys=True).encode(), quota=True)
            os.replace(temporary, MARKER, src_dir_fd=work, dst_dir_fd=work)
            os.fsync(work)
        finally:
            try:
                os.unlink(temporary, dir_fd=work)
            except FileNotFoundError:
                pass

    def bind_output(self, work, marker, output_fd):
        marker["output_identity"] = _identity(output_fd)
        self.write(work, marker)

    def bind_receipt(self, work, marker, receipt):
        marker["receipt_fingerprint"] = canonical_fingerprint(receipt)
        self.write(work, marker)

    def _read(self, work, name):
        try:
            marker = json.loads(ws.read(work, MARKER, 16384))
            required = {"marker_version", "operation", "project_id", "project_identity", "workspace_identity",
                "action", "version", "parent_version", "parent_fingerprint", "source_receipt_version",
                "source_fingerprint", "output_identity", "receipt_fingerprint", "seal"}
            if not isinstance(marker, dict) or set(marker) != required:
                raise ValueError("marker fields differ")
            body = {key: value for key, value in marker.items() if key != "seal"}
            expected = hmac.new(self.key, canonical_fingerprint(body).encode(), hashlib.sha256).hexdigest()
            if not isinstance(marker["seal"], str) or not hmac.compare_digest(expected, marker["seal"]):
                raise ValueError("marker seal mismatch")
            if marker["marker_version"] != "1" or marker["operation"] != name or marker["project_id"] != self.project_id:
                raise ValueError("marker operation identity mismatch")
            if marker["project_identity"] != _identity(self.project_fd) or marker["workspace_identity"] != _identity(work):
                raise ValueError("marker directory identity mismatch")
            if marker["action"] not in {"seed", "evidence", "validate", "render"} or not _VERSION.fullmatch(marker["version"]):
                raise ValueError("marker action/version invalid")
            return marker
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ReelBenchRecoveryRequiredError("operation marker requires manual recovery") from exc

    def recover(self, service, guard):
        """Only a sealed exact operation with no live directory-lock owner is eligible."""
        try:
            return self._recover(service, guard)
        except (VersionCommitIndeterminateError, ReelBenchRecoveryRequiredError):
            raise
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ReelBenchRecoveryRequiredError("operation state requires manual recovery") from exc

    def _recover(self, service, guard):
        work_names = []
        with os.scandir(self.project_fd) as entries:
            for entry in entries:
                if entry.name.startswith(".reelbench-work-"):
                    if not _NAME.fullmatch(entry.name) or not entry.is_dir(follow_symlinks=False):
                        raise ReelBenchRecoveryRequiredError("unknown workspace requires manual recovery")
                    work_names.append(entry.name)
                    if len(work_names) > 1:
                        raise ReelBenchRecoveryRequiredError("multiple orphan operations require manual recovery")
        for name in work_names:
            with ws.directory(self.project_fd, name) as work:
                try:
                    fcntl.flock(work, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ReelBenchRecoveryRequiredError("operation still has a live owner") from exc
                marker = self._read(work, name)
                source = service._read_version(self.project_fd, "source_receipt", marker["source_receipt_version"])
                if canonical_fingerprint(source) != marker["source_fingerprint"]:
                    raise ReelBenchRecoveryRequiredError("operation source fingerprint changed")
                version = marker["version"]
                try:
                    receipt = service._read_version(self.project_fd, "reelbench_evidence", version)
                except FileNotFoundError:
                    receipt = None
                if receipt is not None:
                    if marker["receipt_fingerprint"] is None or canonical_fingerprint(receipt) != marker["receipt_fingerprint"]:
                        raise ReelBenchRecoveryRequiredError("visible operation receipt differs from sealed marker")
                    service._lineage(self.project_fd, self.project_id, version, source)
                    guard()
                    ws.remove_tree(self.project_fd, name, expected=marker["workspace_identity"])
                    raise VersionCommitIndeterminateError(project_id=self.project_id, family="reelbench_evidence",
                        version=version, path=self.project_path / "reelbench_evidence" / f"{version}.json",
                        payload_fingerprint=marker["receipt_fingerprint"])
                latest = service._latest(self.project_fd)
                expected = f"v{int(latest[1:]) + 1 if latest else 1:03d}"
                if version != expected or marker["parent_version"] != latest:
                    raise ReelBenchRecoveryRequiredError("orphan version or parent no longer matches")
                parent_fingerprint = None if latest is None else service._read_version(
                    self.project_fd, "reelbench_evidence", latest)["evidence_fingerprint"]
                if parent_fingerprint != marker["parent_fingerprint"]:
                    raise ReelBenchRecoveryRequiredError("orphan parent fingerprint changed")
                try:
                    with ws.directory(self.project_fd, f"reelbench/{version}") as output:
                        if marker["output_identity"] != _identity(output):
                            raise ReelBenchRecoveryRequiredError("orphan output identity is unknown")
                    guard()
                    with ws.directory(self.project_fd, "reelbench") as family:
                        ws.remove_tree(family, version, expected=marker["output_identity"])
                except FileNotFoundError:
                    if marker["output_identity"] is not None:
                        raise ReelBenchRecoveryRequiredError("sealed orphan output is missing")
                guard()
                ws.remove_tree(self.project_fd, name, expected=marker["workspace_identity"])
        # A directory without its exact marker is not attributed to this service.
        try:
            with ws.directory(self.project_fd, "reelbench") as family:
                with os.scandir(family) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 256 or not _VERSION.fullmatch(entry.name) or not entry.is_dir(follow_symlinks=False):
                            raise ReelBenchRecoveryRequiredError("unknown artifact directory requires manual recovery")
                        try:
                            service._read_version(self.project_fd, "reelbench_evidence", entry.name)
                        except FileNotFoundError as exc:
                            raise ReelBenchRecoveryRequiredError("orphan artifact has no sealed operation marker") from exc
        except FileNotFoundError:
            pass
