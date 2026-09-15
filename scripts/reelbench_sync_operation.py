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
import sys
from contextlib import ExitStack, contextmanager, nullcontext

from scripts import reelbench_workspace as ws
from scripts.json_contracts import canonical_fingerprint
from scripts.video_project_store import VersionCommitIndeterminateError

MARKER = ".reelbench-operation.json"
KEY = ".reelbench-operation-key"
_NAME = re.compile(r"\.reelbench-sync-work-[a-f0-9]{24}")
_VERSION = re.compile(r"v[0-9]{3,}")
_CLEANUP = '.reelbench-sync-cleanup-'


class ReelBenchRecoveryRequiredError(ValueError):
    """An unknown, live, or unsealed operation requires manual recovery."""


def _identity(fd):
    info = os.fstat(fd)
    return {"device": info.st_dev, "inode": info.st_ino}


class SyncOperationJournal:
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
            primary = sys.exc_info()[1]
            try:
                os.unlink(temporary, dir_fd=work)
            except FileNotFoundError:
                pass
            except BaseException as exc:
                ws.cleanup_failure(primary, exc)

    def bind_output(self, work, marker, output_fd):
        marker["output_identity"] = _identity(output_fd)
        self.write(work, marker)

    def bind_receipt(self, work, marker, receipt):
        marker["receipt_fingerprint"] = canonical_fingerprint(receipt)
        self.write(work, marker)

    def _read(self, work, name, payload=None):
        try:
            marker = json.loads(ws.read(work, MARKER, 16384)) if payload is None else payload
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
            if marker["project_identity"] != _identity(self.project_fd) or (work is not None and marker["workspace_identity"] != _identity(work)):
                raise ValueError("marker directory identity mismatch")
            if marker["action"] not in {"plan", "panels", "export", "verify"} or not _VERSION.fullmatch(marker["version"]):
                raise ValueError("marker action/version invalid")
            return marker
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ReelBenchRecoveryRequiredError("operation marker requires manual recovery") from exc

    def _save_cleanup(self, record):
        body = {key: value for key, value in record.items() if key != 'seal'}
        record = {**body, 'seal': hmac.new(self.key, canonical_fingerprint(body).encode(), hashlib.sha256).hexdigest()}
        name = _CLEANUP + record['marker']['operation'][len('.reelbench-sync-work-'):] + '.json'
        temporary = '.cleanup-tmp-' + secrets.token_hex(12)
        try:
            ws.write(self.project_fd, temporary, json.dumps(record, sort_keys=True).encode())
            os.replace(temporary, name, src_dir_fd=self.project_fd, dst_dir_fd=self.project_fd)
            os.fsync(self.project_fd)
        finally:
            primary = sys.exc_info()[1]
            try:
                os.unlink(temporary, dir_fd=self.project_fd)
            except FileNotFoundError:
                pass
            except BaseException as exc:
                ws.cleanup_failure(primary, exc)
        return name

    def _finish_cleanup(self, record, guard):
        """A sealed external intent survives partial deletion of the workspace."""
        marker = record['marker']
        name = _CLEANUP + marker['operation'][len('.reelbench-sync-work-'):] + '.json'
        if record['phase'] == 'output':
            if marker['output_identity'] is not None:
                guard()
                with ws.directory(self.project_fd, 'reelbench_sync_media') as family:
                    self._remove_if_present(family, marker['version'], marker['output_identity'])
            record['phase'] = 'workspace'
            self._save_cleanup(record)
        if record['phase'] == 'workspace':
            guard()
            self._remove_if_present(self.project_fd, marker['operation'], marker['workspace_identity'])
            record['phase'] = 'complete'
            self._save_cleanup(record)
        guard()
        os.unlink(name, dir_fd=self.project_fd)
        os.fsync(self.project_fd)

    @staticmethod
    def _remove_if_present(fd, name, identity):
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            # A previous rmdir may have succeeded before its parent fsync failed.
            # Absence is durable only after this retry crosses the same barrier.
            os.fsync(fd)
            return
        ws.remove_tree(fd, name, expected=identity)

    @contextmanager
    def _verified_publication(self, marker):
        """Keep the verified receipt's exact identity through all later cleanup."""
        error = VersionCommitIndeterminateError(project_id=self.project_id, family='reelbench_sync',
            version=marker['version'], path=self.project_path / 'reelbench_sync' / (marker['version'] + '.json'),
            payload_fingerprint=marker['receipt_fingerprint'])
        try:
            yield
        except BaseException as exc:
            raise error from exc
        raise error

    def cleanup(self, work, name, *, published, guard):
        marker = self._read(work, name)
        if not published:
            try:
                with ws.directory(self.project_fd, 'reelbench_sync_media/' + marker['version']) as output:
                    if _identity(output) != marker['output_identity']:
                        raise ReelBenchRecoveryRequiredError('cleanup output identity changed')
            except FileNotFoundError:
                if marker['output_identity'] is not None:
                    raise ReelBenchRecoveryRequiredError('cleanup output is missing before sealed intent')
        record = {'marker': marker, 'phase': 'workspace' if published else 'output', 'published': published}
        self._save_cleanup(record)
        self._finish_cleanup(record, guard)

    def _resume_cleanups(self, service, guard):
        for name in os.listdir(self.project_fd):
            if not name.startswith(_CLEANUP):
                continue
            record = json.loads(ws.read(self.project_fd, name, 32768))
            if set(record) != {'marker', 'phase', 'seal', 'published'} or type(record['published']) is not bool or record['phase'] not in {'output', 'workspace', 'complete'}:
                raise ReelBenchRecoveryRequiredError('invalid cleanup phase')
            body = {k: v for k, v in record.items() if k != 'seal'}
            expected = hmac.new(self.key, canonical_fingerprint(body).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, record['seal']):
                raise ReelBenchRecoveryRequiredError('cleanup seal mismatch')
            marker = self._read(None, record['marker']['operation'], record['marker'])
            if name != _CLEANUP + marker['operation'][len('.reelbench-sync-work-'):] + '.json':
                raise ReelBenchRecoveryRequiredError('cleanup name mismatch')
            source = service._read_version(self.project_fd, 'source_receipt', marker['source_receipt_version'])
            if canonical_fingerprint(source) != marker['source_fingerprint']:
                raise ReelBenchRecoveryRequiredError('cleanup source changed')
            latest = marker['version'] if record['published'] else marker['parent_version']
            if service._latest(self.project_fd) != latest:
                raise ReelBenchRecoveryRequiredError('cleanup parent changed')
            if marker['parent_version'] is not None:
                parent = service._read_version(self.project_fd, 'reelbench_sync', marker['parent_version'])
                if parent['evidence_fingerprint'] != marker['parent_fingerprint']:
                    raise ReelBenchRecoveryRequiredError('cleanup parent fingerprint changed')
            if record['published']:
                if record['phase'] == 'output':
                    raise ReelBenchRecoveryRequiredError('published cleanup cannot delete output')
                receipt = service._read_version(self.project_fd, 'reelbench_sync', marker['version'])
                if canonical_fingerprint(receipt) != marker['receipt_fingerprint']:
                    raise ReelBenchRecoveryRequiredError('cleanup visible receipt fingerprint changed')
                service._lineage(self.project_fd, self.project_id, marker['version'], source)
            publication = self._verified_publication(marker) if record['published'] else nullcontext()
            with publication, ExitStack() as stack:
                try:
                    work = stack.enter_context(ws.directory(self.project_fd, marker['operation']))
                except FileNotFoundError:
                    work = None
                    if record['phase'] == 'output':
                        raise ReelBenchRecoveryRequiredError('cleanup workspace disappeared before output cleanup')
                if work is not None:
                    if _identity(work) != marker['workspace_identity']:
                        raise ReelBenchRecoveryRequiredError('cleanup workspace identity changed')
                    try:
                        fcntl.flock(work, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as exc:
                        raise ReelBenchRecoveryRequiredError('cleanup has live owner') from exc
                self._finish_cleanup(record, guard)

    def recover(self, service, guard):
        """Only a sealed exact operation with no live directory-lock owner is eligible."""
        try:
            return self._recover(service, guard)
        except (VersionCommitIndeterminateError, ReelBenchRecoveryRequiredError):
            raise
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ReelBenchRecoveryRequiredError("operation state requires manual recovery") from exc

    def _recover(self, service, guard):
        self._resume_cleanups(service, guard)
        work_names = []
        with os.scandir(self.project_fd) as entries:
            for entry in entries:
                if entry.name.startswith(".reelbench-sync-work-"):
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
                    receipt = service._read_version(self.project_fd, "reelbench_sync", version)
                except FileNotFoundError:
                    receipt = None
                if receipt is not None:
                    if marker["receipt_fingerprint"] is None or canonical_fingerprint(receipt) != marker["receipt_fingerprint"]:
                        raise ReelBenchRecoveryRequiredError("visible operation receipt differs from sealed marker")
                    service._lineage(self.project_fd, self.project_id, version, source)
                    with self._verified_publication(marker):
                        guard()
                        self.cleanup(work, name, published=True, guard=guard)
                latest = service._latest(self.project_fd)
                expected = f"v{int(latest[1:]) + 1 if latest else 1:03d}"
                if version != expected or marker["parent_version"] != latest:
                    raise ReelBenchRecoveryRequiredError("orphan version or parent no longer matches")
                parent_fingerprint = None if latest is None else service._read_version(
                    self.project_fd, "reelbench_sync", latest)["evidence_fingerprint"]
                if parent_fingerprint != marker["parent_fingerprint"]:
                    raise ReelBenchRecoveryRequiredError("orphan parent fingerprint changed")
                try:
                    with ws.directory(self.project_fd, f"reelbench_sync_media/{version}") as output:
                        if marker["output_identity"] != _identity(output):
                            raise ReelBenchRecoveryRequiredError("orphan output identity is unknown")
                    guard()
                except FileNotFoundError:
                    if marker["output_identity"] is not None:
                        raise ReelBenchRecoveryRequiredError("sealed orphan output is missing")
                guard()
                record = {'marker': marker, 'phase': 'output', 'published': False}
                self._save_cleanup(record)
                self._finish_cleanup(record, guard)
        # A directory without its exact marker is not attributed to this service.
        try:
            with ws.directory(self.project_fd, "reelbench_sync_media") as family:
                with os.scandir(family) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 256 or not _VERSION.fullmatch(entry.name) or not entry.is_dir(follow_symlinks=False):
                            raise ReelBenchRecoveryRequiredError("unknown artifact directory requires manual recovery")
                        try:
                            service._read_version(self.project_fd, "reelbench_sync", entry.name)
                        except FileNotFoundError as exc:
                            raise ReelBenchRecoveryRequiredError("orphan artifact has no sealed operation marker") from exc
        except FileNotFoundError:
            pass

