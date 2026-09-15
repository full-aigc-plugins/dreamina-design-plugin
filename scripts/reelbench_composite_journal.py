"""Sealed intent and durable outcome for a subject plus comparison sidecar."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import sys

from scripts import reelbench_workspace as ws
from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_operation import OperationJournal, ReelBenchRecoveryRequiredError


class CompositeJournal:
    """One private signed record; completed records remain as idempotency receipts."""

    def __init__(self, store_fd, project_fd, project_id, root):
        self.fd = project_fd
        self.project_id = project_id
        self.root = root
        self.key = OperationJournal(store_fd, project_fd, project_id, root).key
        info = os.fstat(project_fd)
        self.identity = {"device": info.st_dev, "inode": info.st_ino}

    def path(self, operation_id, *, complete=False):
        return f".reelbench-{'completed' if complete else 'composites'}/{operation_id}.json"

    def _seal(self, record):
        body = {k: v for k, v in record.items() if k != "seal"}
        return {**body, "seal": hmac.new(self.key, canonical_fingerprint(body).encode(), hashlib.sha256).hexdigest()}

    def validate(self, record, operation_id):
        required = {"journal_version", "operation_id", "project_id", "project_identity", "request_fingerprint",
                    "comparison_version", "comparison_fingerprint", "subject_family", "subject", "binding", "phase", "seal"}
        try:
            if not isinstance(record, dict) or set(record) != required:
                raise ValueError("journal fields differ")
            if not isinstance(record["seal"], str) or not hmac.compare_digest(record["seal"], self._seal(record)["seal"]):
                raise ValueError("journal seal differs")
            if (record["journal_version"] != "1" or record["operation_id"] != operation_id
                or record["project_id"] != self.project_id or record["project_identity"] != self.identity
                or record["phase"] not in {"reserved", "subject", "binding", "complete"}):
                raise ValueError("journal identity or phase differs")
        except (KeyError, TypeError, ValueError) as exc:
            raise ReelBenchRecoveryRequiredError("composite journal is forged, stale, or belongs to another project") from exc
        return record

    def load(self, operation_id):
        for complete in (False, True):
            name = self.path(operation_id, complete=complete)
            try:
                with ws.file_at(self.fd, name) as fd:
                    info = os.fstat(fd)
                    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                        raise ReelBenchRecoveryRequiredError("composite journal must be private")
                record = json.loads(ws.read(self.fd, name))
            except FileNotFoundError:
                continue
            self.validate(record, operation_id)
            if complete and record["phase"] != "complete":
                raise ReelBenchRecoveryRequiredError("completed journal has an unfinished phase")
            return record
        return None

    def save(self, record, guard):
        sealed = self._seal(record)
        name = self.path(record["operation_id"])
        directory = name.split('/')[0]
        ws.mkdir(self.fd, directory)
        temporary = f"{directory}/.tmp-{secrets.token_hex(12)}"
        try:
            ws.write(self.fd, temporary, json.dumps(sealed, sort_keys=True).encode())
            guard()
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            with ws.directory(self.fd, directory) as fd:
                os.fsync(fd)
            os.fsync(self.fd)
        finally:
            primary = sys.exc_info()[1]
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass
            except BaseException as exc:
                ws.cleanup_failure(primary, exc)
        record.update(sealed)

    def finish(self, record, guard):
        # Keep the signed complete outcome, so a crash after unlink/fsync can
        # still prove the exact subject and sidecar without a replacement version.
        record["phase"] = "complete"
        self.save(record, guard)
        ws.mkdir(self.fd, ".reelbench-completed")
        guard()
        target = self.path(record["operation_id"], complete=True)
        os.replace(self.path(record["operation_id"]), target, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        for directory in (".reelbench-composites", ".reelbench-completed"):
            with ws.directory(self.fd, directory) as fd:
                os.fsync(fd)
        from scripts.video_project_store import VideoProjectStore
        with VideoProjectStore._reservation_lock(self.fd):
            for reservation in (record["subject"], record["binding"]):
                try:
                    os.unlink(f".reservations/{reservation['operation_id']}.json", dir_fd=self.fd)
                except FileNotFoundError:
                    pass
            with ws.directory(self.fd, ".reservations") as fd:
                os.fsync(fd)
        os.fsync(self.fd)
        guard()
