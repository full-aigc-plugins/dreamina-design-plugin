"""Immutable project evidence produced entirely within a pinned action workspace."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
import fcntl
import threading
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scripts import reelbench_workspace as ws
from scripts.json_contracts import canonical_fingerprint, validate_contract
from scripts.reelbench_adapter import ReelBenchAdapter
from scripts.reelbench_contracts import validate_reelbench_evidence
from scripts.video_project_store import VersionCommitIndeterminateError, VideoProjectStore
from scripts.reelbench_operation import OperationJournal

# Two frames per shot plus ceil(shots / 25) sheets per pick fit 256 artifacts.
MAX_SHOTS = 120
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_DOCUMENT_BYTES = ws.MAX_FILE_BYTES
MAX_REPORT_BYTES = 4 * 1024 * 1024
_VERSION = re.compile(r"v[0-9]{3,}")
_MIME = {".json": "application/json", ".md": "text/markdown", ".html": "text/html", ".jpg": "image/jpeg"}
_ANCESTRY_LOCK = threading.RLock()


class ReelBenchProjectIdentityError(ValueError):
    """The project entry no longer names the locked project inode."""


class ReelBenchProjectService:
    """Serialize an action on the project directory inode, never a replaceable lockfile."""

    def __init__(self, store: VideoProjectStore, adapter: ReelBenchAdapter) -> None:
        self._store = store
        self._adapter = adapter

    def run(self, project_id: str, *, action: str, expected_parent: str | None,
            source_receipt_version: str, **options: Any) -> dict[str, Any]:
        if action not in {"seed", "evidence", "validate", "render"}:
            raise ValueError("unsupported ReelBench action")
        self._version(expected_parent, nullable=True)
        self._version(source_receipt_version)
        result = None
        try:
            with self._locked_project(project_id) as (store_fd, descriptor, root, guard):
                journal = OperationJournal(store_fd, descriptor, project_id, root)
                journal.recover(self, guard)
                result = self._execute(descriptor, root, project_id, action, expected_parent,
                                       source_receipt_version, options, guard, journal)
                guard()
            return result
        except BaseException as exc:
            if result is not None and not isinstance(exc, VersionCommitIndeterminateError):
                raise VersionCommitIndeterminateError(project_id=project_id, family="reelbench_evidence",
                    version=result["version"], path=root / "reelbench_evidence" / f"{result['version']}.json",
                    payload_fingerprint=canonical_fingerprint(result)) from exc
            raise

    @contextmanager
    def _locked_project(self, project_id):
        self._store._validate_project_id(project_id)
        with ws.absolute_chain(self._store._root) as (store_fd, anchor, verify_store):
            # The filesystem-root inode survives replacement of any store ancestor.
            # Its advisory lock serializes cooperating ReelBench writers globally.
            with _ANCESTRY_LOCK:
                fcntl.flock(anchor, fcntl.LOCK_EX)
                owned = []
                try:
                    verify_store()
                    descriptor = os.open(project_id, ws.DIRECTORY, dir_fd=store_fd)
                    owned.append(descriptor)
                    info = os.fstat(descriptor)
                    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                        raise ValueError("project root must be private")
                    def guard():
                        verify_store()
                        self._assert_project(store_fd, descriptor, project_id)
                    guard()
                    yield store_fd, descriptor, self._store._root / project_id, guard
                finally:
                    ws._close_owned(owned)

    @classmethod
    def _assert_project(cls, store_fd, project_fd, project_id):
        try:
            entry = os.stat(project_id, dir_fd=store_fd, follow_symlinks=False)
            if cls._directory_identity(entry) != cls._directory_identity(os.fstat(project_fd)):
                raise ReelBenchProjectIdentityError("project entry identity changed")
        except OSError as exc:
            raise ReelBenchProjectIdentityError("project entry identity changed") from exc

    def _execute(self, project_fd, root, project_id, action, parent, source_version, options, guard, journal):
        source = self._read_version(project_fd, "source_receipt", source_version)
        validate_contract(source, "source_receipt.schema.json")
        if source["project_id"] != project_id:
            raise ValueError("source receipt project mismatch")
        duration = source["duration_seconds"]
        if not math.isfinite(duration) or not 0 < duration <= 1800 or source["size_bytes"] > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds preventive duration or byte bound")
        latest = self._latest(project_fd)
        if parent != latest:
            raise ValueError("expected parent does not match latest ReelBench evidence version")
        version = f"v{int(latest[1:]) + 1 if latest else 1:03d}"
        lineage = self._lineage(project_fd, project_id, parent, source)
        allowed = {"seed": {"threshold", "title"}, "evidence": set(), "validate": set(), "render": {"mode"}}[action]
        if set(options) - allowed:
            raise ValueError("unsupported ReelBench action options")
        if action != "seed" and not lineage:
            raise ValueError(f"{action} requires an exact parent")
        if action == "render" and (lineage[0]["action"] != "validate" or any(g["status"] == "FAIL" for g in lineage[0]["gates"])):
            raise ValueError("render requires an immediate validated parent")
        name = ".reelbench-work-" + secrets.token_hex(12)
        os.mkdir(name, 0o700, dir_fd=project_fd)
        work = None
        output_fd = None
        published = False
        output_created = False
        marker_created = False
        error = None
        try:
            work = os.open(name, ws.DIRECTORY, dir_fd=project_fd)
            marker = journal.create(work, name, action=action, version=version, parent=parent,
                parent_fingerprint=lineage[0]["evidence_fingerprint"] if lineage else None, source=source)
            marker_created = True
            os.fsync(project_fd)
            source_relative = Path(source["staged_path"]).relative_to(root).as_posix()
            if not source_relative.startswith("source/"):
                raise ValueError("source receipt escaped project source")
            source_name = "source/" + source["source_sha256"]
            with ws.file_at(project_fd, source_relative) as source_fd:
                info = os.fstat(source_fd)
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise ValueError("source is not private")
                source_identity = ws.identity(info)
            ws.copy(project_fd, source_relative, work, source_name, maximum=MAX_SOURCE_BYTES,
                    expected={"sha256": source["source_sha256"], "size_bytes": source["size_bytes"]}, mode=0o400, quota=True)
            consumed = [{"version": source_version, "receipt_fingerprint": canonical_fingerprint(source),
                "path": source_relative, "workspace_path": source_name, "sha256": source["source_sha256"],
                "size_bytes": source["size_bytes"]}]
            selected = {}
            if action != "seed":
                # Newest produced artifact wins. Every ancestor was validated above.
                for receipt in lineage:
                    for artifact in receipt["artifacts"]:
                        relative = artifact["path"].split("/", 2)[2]
                        if relative not in selected and (relative in {"shots.json", "track.json"} or
                                (action in {"validate", "render"} and relative.startswith("frames/"))):
                            selected[relative] = (receipt, artifact)
                    if receipt["action"] == "seed":
                        break
                if not {"shots.json", "track.json"} <= set(selected):
                    raise ValueError("lineage lacks required shots and track artifacts")
                if action in {"validate", "render"} and not any(name.startswith("frames/") for name in selected):
                    raise ValueError("current seed has no frame evidence")
                for relative, (receipt, artifact) in selected.items():
                    target = "inputs/" + relative
                    ws.copy(project_fd, artifact["path"], work, target, maximum=MAX_DOCUMENT_BYTES,
                            expected=artifact, mode=0o400, quota=True)
                    consumed.append({"version": receipt["version"], "receipt_fingerprint": receipt["evidence_fingerprint"],
                        "path": artifact["path"], "workspace_path": target,
                        "sha256": artifact["sha256"], "size_bytes": artifact["size_bytes"]})
            ws.mkdir(work, "output")
            with self._adapter.execution_workspace(work, consumed):
                results = self._run_action(work, action, source_name, options, source_relative)
                # Validate and fsync every generated byte before creating visible artifacts.
                generated = ws.inventory(work, "output")
                self._complete(work, action, generated, options)
                for entry in consumed:
                    with ws.file_at(project_fd, entry["path"]) as fd:
                        if entry["path"] == source_relative and ws.identity(os.fstat(fd)) != source_identity:
                            raise ValueError("source identity changed during execution")
                    # Recheck copied inputs, including all frames, after the child.
                    copied = self._digest(work, entry["workspace_path"], MAX_SOURCE_BYTES)
                    if copied != {k: entry[k] for k in ("sha256", "size_bytes")}:
                        raise ValueError("consumed artifact changed during execution")
                guard()
                ws.mkdir(project_fd, "reelbench")
                with ws.directory(project_fd, "reelbench") as family:
                    os.mkdir(version, 0o700, dir_fd=family)
                    output_created = True
                    output_fd = os.open(version, ws.DIRECTORY, dir_fd=family)
                    os.fsync(family)
                    journal.bind_output(work, marker, output_fd)
                artifacts = []
                for relative, expected in sorted(generated.items()):
                    guard()
                    target = f"reelbench/{version}/{relative}"
                    ws.copy(work, "output/" + relative, output_fd, relative,
                            maximum=MAX_DOCUMENT_BYTES, expected=expected)
                    artifacts.append({"path": target, "mime_type": _MIME[Path(relative).suffix], **expected})
                os.fsync(output_fd)
                with ws.directory(project_fd, f"reelbench/{version}") as check:
                    if self._directory_identity(os.fstat(check)) != self._directory_identity(os.fstat(output_fd)):
                        raise ValueError("artifact publication directory identity changed")
                os.fsync(project_fd)
                shots_path = "output/shots.json" if action == "seed" else "inputs/shots.json"
                shots_bytes = ws.read(work, shots_path)
                shots = {"sha256": hashlib.sha256(shots_bytes).hexdigest(),
                         "ids": [item["id"] for item in self._shots(shots_bytes)["shots"]]}
                receipt = self._receipt(project_id, version, parent, source, lineage, action, results, artifacts, consumed, shots)
                validate_reelbench_evidence(receipt)
                journal.bind_receipt(work, marker, receipt)
                error = VersionCommitIndeterminateError(project_id=project_id, family="reelbench_evidence",
                    version=version, path=root / "reelbench_evidence" / f"{version}.json",
                    payload_fingerprint=canonical_fingerprint(receipt))
                try:
                    guard()
                    persisted = self._store.write_version(project_id, "reelbench_evidence", receipt,
                        schema_name="reelbench_evidence.schema.json", version=version, project_fd=project_fd,
                        publication_guard=guard)
                except VersionCommitIndeterminateError:
                    published = True
                    raise
                except BaseException as exc:
                    try:
                        visible = self._read_version(project_fd, "reelbench_evidence", version)
                        published = canonical_fingerprint(visible) == error.payload_fingerprint
                    except BaseException:
                        # If publication cannot be disproved after an unexpected
                        # store failure, preserve artifacts and demand exact recovery.
                        try:
                            with ws.file_at(project_fd, f"reelbench_evidence/{version}.json"):
                                published = True
                        except FileNotFoundError:
                            published = False
                        except BaseException:
                            published = True
                    if published:
                        raise error from exc
                    raise
                published = True
                try:
                    validate_reelbench_evidence(persisted)
                    guard()
                except BaseException as exc:
                    raise error from exc
                return persisted
        except BaseException as exc:
            if published and error is not None and not isinstance(exc, VersionCommitIndeterminateError):
                raise error from exc
            raise
        finally:
            primary = sys.exc_info()[1]
            cleanup_error = None
            try:
                if work is None:
                    os.rmdir(name, dir_fd=project_fd)
                elif self._directory_identity(os.stat(name, dir_fd=project_fd, follow_symlinks=False)) != self._directory_identity(os.fstat(work)):
                    raise ValueError("workspace identity changed before cleanup")
                elif marker_created:
                    journal.cleanup(work, name, published=published, guard=guard)
                else:
                    ws.remove_tree(project_fd, name)
                if output_created and not published and not marker_created:
                    with ws.directory(project_fd, "reelbench") as family:
                        if output_fd is None or self._directory_identity(os.stat(version, dir_fd=family, follow_symlinks=False)) != self._directory_identity(os.fstat(output_fd)):
                            raise ValueError("output identity changed before cleanup")
                        ws.remove_tree(family, version)
            except BaseException as exc:
                cleanup_error = exc
            finally:
                for descriptor in (output_fd, work):
                    if descriptor is not None:
                        try:
                            ws._close_owned([descriptor])
                        except BaseException as exc:
                            if cleanup_error is None:
                                cleanup_error = exc
            if cleanup_error is not None:
                if primary is not None:
                    ws.cleanup_failure(primary, cleanup_error)
                elif published and error is not None:
                    raise error from cleanup_error
                else:
                    raise cleanup_error

    def _run_action(self, work, action, source_name, options, source_relative):
        base = Path(f"/dev/fd/{work}")
        source = base / source_name
        out = base / "output"
        if action == "seed":
            result = self._adapter.seed(source=source, shots=out / "shots.json", track=out / "track.json",
                threshold=options.get("threshold", 0.3), title=options.get("title", "Reference"))
            document = self._shots(result.stdout.encode())
            ws.write(work, "output/shots.json", result.stdout.encode(), quota=True)
            self._json(ws.read(work, "output/track.json"))
            return [result]
        shots = base / "inputs/shots.json"
        track = base / "inputs/track.json"
        document = self._shots(ws.read(work, "inputs/shots.json"))
        self._json(ws.read(work, "inputs/track.json"))
        if action == "evidence":
            frames, sheets = out / "frames", out / "sheets"
            first = self._adapter.frames(shots=shots, source=source, frames_dir=frames)
            self._expect_frames(ws.inventory(work, "output/frames"), document)
            return [first, self._adapter.sheet(shots=shots, frames_dir=frames, sheets_dir=sheets, pick="a"),
                    self._adapter.sheet(shots=shots, frames_dir=frames, sheets_dir=sheets, pick="b")]
        self._expect_frames(ws.inventory(work, "inputs/frames"), document)
        frames = base / "inputs/frames"
        if action == "validate":
            return [self._adapter.validate(shots=shots, track=track, frames_dir=frames)]
        mode = options.get("mode", "md")
        result = self._adapter.render(shots=shots, track=track, frames_dir=frames, source=source, mode=mode)
        payload = result.stdout.encode()
        if not payload or len(payload) > MAX_REPORT_BYTES:
            raise ValueError("report is empty or oversized")
        if mode == "html":
            # Rebase only the player's service-owned URL. The unchanged upstream
            # report's private workspace goes away; the project source remains.
            marker = f' src="{source_name}"'.encode()
            if payload.count(marker) != 1:
                raise ValueError("report player source is not complete")
            url = "../../" + quote(source_relative, safe="/")
            payload = payload.replace(marker, f' src="{url}"'.encode(), 1)
        ws.write(work, f"output/report.{mode}", payload, quota=True)
        # Offline reports must retain their relative frame references after publishing.
        if mode == "html":
            for name, expected in ws.inventory(work, "inputs/frames").items():
                ws.copy(work, "inputs/frames/" + name, work, "output/inputs/frames/" + name,
                        maximum=MAX_DOCUMENT_BYTES, expected=expected, quota=True)
        return [result]

    def _complete(self, work, action, generated, options):
        if action == "seed":
            expected = {"shots.json", "track.json"}
        elif action == "evidence":
            doc = self._shots(ws.read(work, "inputs/shots.json"))
            count = math.ceil(len(doc["shots"]) / 25)
            expected = {f"frames/{shot['id']}{pick}.jpg" for shot in doc["shots"] for pick in ("a", "b")}
            expected |= {f"sheets/sheet-{pick}{page:02d}.jpg" for pick in ("a", "b") for page in range(1, count + 1)}
        elif action == "validate":
            expected = set()
        else:
            mode = options.get("mode", "md")
            expected = {f"report.{mode}"}
            if mode == "html":
                expected |= {"inputs/frames/" + name for name in ws.inventory(work, "inputs/frames")}
        if set(generated) != expected:
            raise ValueError("generated artifact inventory is not complete")

    def _lineage(self, fd, project_id, parent, source):
        receipts = []
        current = parent
        expected_fingerprint = None
        while current is not None:
            if len(receipts) >= 256:
                raise ValueError("lineage exceeds bound")
            receipt = self._read_version(fd, "reelbench_evidence", current)
            validate_reelbench_evidence(receipt)
            if receipt["version"] != current or receipt["project_id"] != project_id or receipt["source_sha256"] != source["source_sha256"] or receipt["source_receipt_version"] != source["version"]:
                raise ValueError("parent receipt source or identity mismatch")
            if expected_fingerprint is not None and receipt["evidence_fingerprint"] != expected_fingerprint:
                raise ValueError("parent fingerprint mismatch")
            observed = ws.inventory(fd, f"reelbench/{current}", private=True)
            expected = {}
            for artifact in receipt["artifacts"]:
                prefix = f"reelbench/{current}/"
                if not artifact["path"].startswith(prefix):
                    raise ValueError("ancestor artifact escaped version")
                expected[artifact["path"][len(prefix):]] = {k: artifact[k] for k in ("sha256", "size_bytes")}
            if observed != expected:
                raise ValueError("ancestor artifact inventory or digest mismatch")
            if receipt["action"] == "seed":
                shots_artifact = next(a for a in receipt["artifacts"] if a["path"].endswith("/shots.json"))
            else:
                shots_artifact = next(a for a in receipt["consumed_artifacts"] if a["workspace_path"] == "inputs/shots.json")
            shots_bytes = ws.read(fd, shots_artifact["path"])
            if hashlib.sha256(shots_bytes).hexdigest() != receipt["shots"]["sha256"]:
                raise ValueError("ancestor shots artifact digest mismatch")
            parsed = {"sha256": hashlib.sha256(shots_bytes).hexdigest(),
                      "ids": [shot["id"] for shot in self._shots(shots_bytes)["shots"]]}
            if parsed != receipt["shots"]:
                raise ValueError("ancestor parsed shots identity mismatch")
            receipts.append(receipt)
            expected_fingerprint = receipt["parent_fingerprint"]
            current = receipt["parent_version"]
        return receipts

    def _receipt(self, project_id, version, parent, source, lineage, action, results, artifacts, consumed, shots):
        lock_root = ws.open_absolute(self._adapter._shots_script.parents[3] / "upstream")
        try:
            lock_bytes = ws.read(lock_root, "reelbench.lock.json")
        finally:
            ws._close_owned([lock_root])
        commands = [{"action": result.action, "argv": result.argv, "returncode": result.returncode,
                     "tool_identities": list(result.tool_identities), "script_manifest": list(result.script_manifest)} for result in results]
        receipt = {"schema_version": "1.1", "version": version, "parent_version": parent,
            "parent_fingerprint": lineage[0]["evidence_fingerprint"] if lineage else None,
            "project_id": project_id, "source_receipt_version": source["version"], "source_sha256": source["source_sha256"],
            "action": action, "upstream": {"source": "https://github.com/eternityspring/reelbench-skills.git",
                "revision": "18f2f63987337df0975a89973d38d50f3231ee31", "lock_fingerprint": hashlib.sha256(lock_bytes).hexdigest()},
            "tool_identities": list(results[0].tool_identities), "commands": commands, "consumed_artifacts": consumed,
            "script_manifest": list(results[0].script_manifest), "shots": shots,
            "argv_fingerprint": canonical_fingerprint({"commands": commands}),
            "gates": list(results[0].gates), "artifacts": artifacts,
            "created_at": _now(), "committed_at": _now()}
        receipt["evidence_fingerprint"] = canonical_fingerprint(receipt)
        return receipt

    def reconcile_indeterminate(self, error):
        if error.family != "reelbench_evidence":
            raise ValueError("indeterminate error is not ReelBench evidence")
        with self._locked_project(error.project_id) as (store_fd, descriptor, _root, guard):
            receipt = self._read_version(descriptor, error.family, error.version)
            if canonical_fingerprint(receipt) != error.payload_fingerprint:
                raise ValueError("indeterminate receipt fingerprint mismatch")
            validate_reelbench_evidence(receipt)
            source = self._read_version(descriptor, "source_receipt", receipt["source_receipt_version"])
            self._lineage(descriptor, error.project_id, receipt["version"], source)
            guard()
            return receipt

    @staticmethod
    def _read_version(fd, family, version):
        ReelBenchProjectService._version(version)
        document = ReelBenchProjectService._json(ws.read(fd, f"{family}/{version}.json"))
        if not isinstance(document, dict) or document.get("version") != version:
            raise ValueError("receipt filename and embedded version differ")
        return document

    @staticmethod
    def _latest(fd):
        try:
            with ws.directory(fd, "reelbench_evidence") as family:
                versions = [name[:-5] for name in os.listdir(family) if name.endswith(".json") and _VERSION.fullmatch(name[:-5])]
            return max(versions, key=lambda v: int(v[1:]), default=None)
        except FileNotFoundError:
            return None

    @staticmethod
    def _version(value, nullable=False):
        if value is None and nullable:
            return
        if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
            raise ValueError("invalid exact version")

    @staticmethod
    def _json(payload):
        if len(payload) > MAX_DOCUMENT_BYTES:
            raise ValueError("JSON exceeds byte bound")
        try:
            value = json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("invalid JSON") from exc
        def check(item, depth):
            if depth > 24:
                raise ValueError("JSON exceeds depth bound")
            if isinstance(item, dict):
                for child in item.values(): check(child, depth + 1)
            elif isinstance(item, list):
                if len(item) > 20000:
                    raise ValueError("JSON exceeds item bound")
                for child in item: check(child, depth + 1)
        check(value, 0)
        return value

    @classmethod
    def _shots(cls, payload):
        doc = cls._json(payload)
        shots = doc.get("shots") if isinstance(doc, dict) else None
        duration = doc.get("meta", {}).get("durationSeconds") if isinstance(doc, dict) and isinstance(doc.get("meta"), dict) else None
        if not isinstance(shots, list) or not 1 <= len(shots) <= MAX_SHOTS:
            raise ValueError("invalid or excessive shot count")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 1800:
            raise ValueError("invalid shot duration")
        ids = []
        for shot in shots:
            if not isinstance(shot, dict) or not isinstance(shot.get("id"), str) or re.fullmatch(r"S[0-9]{2,4}", shot["id"]) is None:
                raise ValueError("unsafe shot id")
            ids.append(shot["id"])
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate shot id")
        return doc

    @staticmethod
    def _expect_frames(inventory, document):
        expected = {f"{s['id']}{pick}.jpg" for s in document["shots"] for pick in ("a", "b")}
        if set(inventory) != expected:
            raise ValueError("frame inventory is not complete")

    @staticmethod
    def _directory_identity(info):
        return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)

    @staticmethod
    def _digest(fd, name, maximum):
        with ws.file_at(fd, name) as source:
            before = os.fstat(source)
            if before.st_size > maximum:
                raise ValueError("input exceeds bound")
            digest, size = hashlib.sha256(), 0
            while chunk := os.read(source, 1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise ValueError("input exceeds bound")
                digest.update(chunk)
            if ws.identity(before) != ws.identity(os.fstat(source)):
                raise ValueError("input changed while hashing")
            return {"sha256": digest.hexdigest(), "size_bytes": size}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
