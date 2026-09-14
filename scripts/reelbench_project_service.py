"""Immutable project versions around guarded ReelBench shot analysis."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.json_contracts import canonical_fingerprint
from scripts.reelbench_adapter import ReelBenchAdapter, ReelBenchAdapterResult
from scripts.reelbench_contracts import validate_reelbench_evidence
from scripts.video_project_store import VersionCommitIndeterminateError, VideoProjectStore


MAX_SHOTS = 10_000
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_FRAMES = MAX_SHOTS * 2
MAX_SHEETS = 256
_VERSION = re.compile(r"v[0-9]{3,}")


class ReelBenchProjectService:
    """Create private ``reelbench_evidence`` versions without replacing prior work."""

    def __init__(self, store: VideoProjectStore, adapter: ReelBenchAdapter) -> None:
        self._store = store
        self._adapter = adapter

    def run(
        self,
        project_id: str,
        *,
        action: str,
        expected_parent: str | None,
        source_receipt_version: str,
        **options: Any,
    ) -> dict[str, Any]:
        """Run one guarded upstream action and publish exactly one immutable receipt."""
        if action not in {"seed", "evidence", "validate", "render"}:
            raise ValueError("unsupported ReelBench action")
        if expected_parent is not None and (not isinstance(expected_parent, str) or _VERSION.fullmatch(expected_parent) is None):
            raise ValueError("expected parent must be an exact version or null")
        source = self._store.read_version(project_id, "source_receipt", source_receipt_version, "source_receipt.schema.json")
        source_path, source_identity = self._verify_source(project_id, source)
        latest = self._latest_version(project_id)
        if expected_parent != latest:
            raise ValueError("expected parent does not match the latest ReelBench evidence version")
        version = self._next_version(project_id)
        output_root = self._create_output_root(project_id, version)
        try:
            result, artifacts = self._run_action(project_id, action, expected_parent, source_path, output_root, options)
            verified_path, verified_identity = self._verify_source(project_id, source)
            if verified_path != source_path or not self._same_identity(source_identity, verified_identity):
                raise ValueError("source pathname identity changed during ReelBench execution")
            receipt = self._receipt(
                project_id=project_id,
                version=version,
                parent_version=expected_parent,
                source=source,
                action=action,
                result=result,
                artifacts=artifacts,
            )
            try:
                persisted = self._store.write_version(
                    project_id,
                    "reelbench_evidence",
                    receipt,
                    schema_name="reelbench_evidence.schema.json",
                    version=version,
                )
            except VersionCommitIndeterminateError as exc:
                return self.reconcile_indeterminate(exc)
            validate_reelbench_evidence(persisted)
            return persisted
        except BaseException:
            self._remove_empty_output_root(output_root)
            raise

    def reconcile_indeterminate(self, error: VersionCommitIndeterminateError) -> dict[str, Any]:
        """Reconcile only the exact version and canonical fingerprint from one durability error."""
        if error.family != "reelbench_evidence":
            raise ValueError("indeterminate error does not belong to ReelBench evidence")
        document = self._store.reconcile_version(
            error.project_id,
            error.family,
            error.version,
            error.payload_fingerprint,
            "reelbench_evidence.schema.json",
        )
        validate_reelbench_evidence(document)
        return document

    def _run_action(
        self,
        project_id: str,
        action: str,
        parent_version: str | None,
        source: Path,
        output_root: Path,
        options: Mapping[str, Any],
    ) -> tuple[ReelBenchAdapterResult, list[dict[str, Any]]]:
        if action == "seed":
            self._reject_unknown(options, {"threshold", "title"})
            shots, track = output_root / "shots.json", output_root / "track.json"
            result = self._adapter.seed(
                source=source, shots=shots, track=track,
                threshold=options.get("threshold", 0.30), title=options.get("title", "Reference"),
            )
            self._verify_shots_output(result.stdout, source)
            self._write_private_bytes(shots, result.stdout.encode("utf-8"), MAX_DOCUMENT_BYTES)
            self._harden_output(track, directory=False)
            return result, [self._artifact(shots), self._artifact(track)]
        if parent_version is None:
            raise ValueError(f"{action} requires an exact ReelBench parent version")
        shots = self._input_artifact(project_id, parent_version, "shots.json")
        track = self._input_artifact(project_id, parent_version, "track.json")
        if action == "evidence":
            self._reject_unknown(options, set())
            frames, sheets = output_root / "frames", output_root / "sheets"
            result = self._adapter.frames(shots=shots, source=source, frames_dir=frames)
            self._adapter.sheet(shots=shots, frames_dir=frames, sheets_dir=sheets, pick="a")
            self._adapter.sheet(shots=shots, frames_dir=frames, sheets_dir=sheets, pick="b")
            self._harden_output(frames, directory=True)
            self._harden_output(sheets, directory=True)
            artifacts = self._tree_artifacts(frames, suffixes={".jpg"}, maximum=MAX_FRAMES)
            artifacts.extend(self._tree_artifacts(sheets, suffixes={".jpg"}, maximum=MAX_SHEETS))
            return result, artifacts
        frames = self._input_artifact(project_id, parent_version, "frames", directory=True)
        if action == "validate":
            self._reject_unknown(options, set())
            return self._adapter.validate(shots=shots, track=track, frames_dir=frames), []
        self._reject_unknown(options, {"mode"})
        mode = options.get("mode", "md")
        result = self._adapter.render(shots=shots, track=track, frames_dir=frames, source=source, mode=mode)
        report = output_root / f"report.{mode}"
        self._write_private_bytes(report, result.stdout.encode("utf-8"), MAX_REPORT_BYTES)
        return result, [self._artifact(report)]

    def _receipt(
        self,
        *,
        project_id: str,
        version: str,
        parent_version: str | None,
        source: Mapping[str, Any],
        action: str,
        result: ReelBenchAdapterResult,
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        created = _now()
        lock_path = self._adapter._shots_script.parents[3] / "upstream" / "reelbench.lock.json"
        lock_bytes = self._read_bounded(lock_path, MAX_DOCUMENT_BYTES)
        receipt: dict[str, Any] = {
            "schema_version": "1.0", "version": version, "parent_version": parent_version,
            "project_id": project_id, "source_receipt_version": source["version"],
            "source_sha256": source["source_sha256"], "action": action,
            "upstream": {
                "source": "https://github.com/eternityspring/reelbench-skills.git",
                "revision": "18f2f63987337df0975a89973d38d50f3231ee31",
                "lock_fingerprint": hashlib.sha256(lock_bytes).hexdigest(),
            },
            "tool_identities": self._adapter.tool_identities,
            "argv_fingerprint": canonical_fingerprint({"argv": result.argv}),
            "gates": list(result.gates), "artifacts": artifacts,
            "created_at": created, "committed_at": _now(),
        }
        receipt["evidence_fingerprint"] = canonical_fingerprint(receipt)
        return receipt

    def _latest_version(self, project_id: str) -> str | None:
        family = self._store.project_root(project_id) / "reelbench_evidence"
        if not family.exists():
            return None
        versions = [path.stem for path in family.glob("v*.json") if _VERSION.fullmatch(path.stem)]
        return max(versions, key=lambda value: int(value[1:]), default=None)

    def _next_version(self, project_id: str) -> str:
        latest = self._latest_version(project_id)
        return f"v{int(latest[1:]) + 1 if latest else 1:03d}"

    def _create_output_root(self, project_id: str, version: str) -> Path:
        family = self._store.project_root(project_id) / "reelbench"
        family.mkdir(mode=0o700, exist_ok=True)
        os.chmod(family, 0o700)
        target = family / version
        try:
            target.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise ValueError("ReelBench output version already exists") from exc
        return target

    def _input_artifact(self, project_id: str, version: str, name: str, *, directory: bool = False) -> Path:
        current: str | None = version
        while current is not None:
            receipt = self._store.read_version(project_id, "reelbench_evidence", current, "reelbench_evidence.schema.json")
            for artifact in receipt["artifacts"]:
                path = self._store.project_root(receipt["project_id"]) / artifact["path"]
                candidate = path
                if directory and name in path.parts:
                    candidate = next(parent for parent in (path, *path.parents) if parent.name == name)
                if candidate.name == name and (candidate.is_dir() if directory else candidate.is_file()):
                    self._verify_artifact(candidate, directory=directory)
                    return candidate
            current = receipt["parent_version"]
        raise ValueError(f"required {name} artifact was not found in the parent lineage")

    def _verify_source(self, project_id: str, source: Mapping[str, Any]) -> tuple[Path, os.stat_result]:
        path = Path(str(source.get("staged_path", "")))
        project_root = self._store.project_root(project_id)
        try:
            if path.is_symlink():
                raise ValueError("source path is a symlink")
            resolved = path.resolve(strict=True)
            resolved.relative_to(project_root / "source")
        except (OSError, ValueError) as exc:
            raise ValueError("source receipt does not point to this private project source") from exc
        self._verify_artifact(resolved, directory=False)
        info = resolved.stat()
        if info.st_size != source.get("size_bytes") or info.st_size > MAX_SOURCE_BYTES:
            raise ValueError("source receipt size no longer matches private source")
        digest, bytes_read = self._digest_bounded(resolved, MAX_SOURCE_BYTES)
        if bytes_read != info.st_size:
            raise ValueError("source changed while its digest was verified")
        if digest != source.get("source_sha256"):
            raise ValueError("source receipt digest no longer matches private source")
        final = resolved.lstat()
        if not self._same_identity(info, final):
            raise ValueError("source pathname identity changed while being verified")
        return resolved, info

    def _verify_shots_output(self, output: str, source: Path) -> None:
        encoded = output.encode("utf-8")
        if len(encoded) > MAX_DOCUMENT_BYTES:
            raise ValueError("shots JSON exceeds the configured byte bound")
        try:
            document = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ValueError("seed output is not JSON") from exc
        if not isinstance(document, dict) or not isinstance(document.get("shots"), list) or len(document["shots"]) > MAX_SHOTS:
            raise ValueError("seed output has an invalid or excessive shot collection")
        duration = document.get("meta", {}).get("durationSeconds") if isinstance(document.get("meta"), dict) else None
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0 or duration > 1800.0:
            raise ValueError("seed output duration is outside the accepted bound")
        if not source.is_file():
            raise ValueError("source changed during ReelBench execution")

    def _tree_artifacts(self, root: Path, *, suffixes: set[str], maximum: int) -> list[dict[str, Any]]:
        self._verify_artifact(root, directory=True)
        paths = [path for path in root.rglob("*") if path.is_file()]
        if len(paths) > maximum or any(len(path.relative_to(root).parts) > 2 or path.suffix not in suffixes for path in paths):
            raise ValueError("ReelBench generated an excessive or unsafe artifact tree")
        return [self._artifact(path) for path in sorted(paths)]

    def _artifact(self, path: Path) -> dict[str, Any]:
        self._verify_artifact(path, directory=False)
        info = path.stat()
        if info.st_size < 1 or info.st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("ReelBench artifact is outside the configured byte bound")
        # ``reelbench/vNNN/<file>`` is the only output layout this service creates.
        reelbench_root = path.parent
        while reelbench_root.name != "reelbench" and reelbench_root != reelbench_root.parent:
            reelbench_root = reelbench_root.parent
        if reelbench_root.name != "reelbench":
            raise ValueError("ReelBench artifact escaped its service-created root")
        project_root = reelbench_root.parent
        relative = path.relative_to(project_root).as_posix()
        mime = {".json": "application/json", ".md": "text/markdown", ".html": "text/html", ".jpg": "image/jpeg"}.get(path.suffix)
        if mime is None:
            raise ValueError("ReelBench artifact media type is not allowlisted")
        digest, bytes_read = self._digest_bounded(path, MAX_ARTIFACT_BYTES)
        final = path.lstat()
        if bytes_read != info.st_size or not self._same_identity(info, final):
            raise ValueError("ReelBench artifact changed while its digest was verified")
        return {"path": relative, "size_bytes": info.st_size, "mime_type": mime, "sha256": digest}

    @staticmethod
    def _verify_artifact(path: Path, *, directory: bool) -> None:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (directory and not stat.S_ISDIR(info.st_mode)) or (not directory and not stat.S_ISREG(info.st_mode)):
            raise ValueError("ReelBench artifact path is unsafe")
        if info.st_mode & 0o077:
            raise ValueError("ReelBench artifact is not private")

    @staticmethod
    def _harden_output(path: Path, *, directory: bool) -> None:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (directory and not stat.S_ISDIR(info.st_mode)) or (not directory and not stat.S_ISREG(info.st_mode)):
            raise ValueError("ReelBench output path is unsafe")
        os.chmod(path, 0o700 if directory else 0o600)
        if directory:
            for child in path.rglob("*"):
                child_info = child.lstat()
                if stat.S_ISLNK(child_info.st_mode) or not stat.S_ISREG(child_info.st_mode):
                    raise ValueError("ReelBench output tree contains an unsafe entry")
                os.chmod(child, 0o600)

    @staticmethod
    def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
        return (first.st_dev, first.st_ino, first.st_size, stat.S_IFMT(first.st_mode), first.st_mode & 0o777) == (second.st_dev, second.st_ino, second.st_size, stat.S_IFMT(second.st_mode), second.st_mode & 0o777)

    @staticmethod
    def _read_bounded(path: Path, maximum: int) -> bytes:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            chunks: list[bytes] = []
            total = 0
            while chunk := os.read(descriptor, min(1024 * 1024, maximum - total + 1)):
                total += len(chunk)
                if total > maximum:
                    raise ValueError("private file exceeds byte bound")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    @staticmethod
    def _digest_bounded(path: Path, maximum: int) -> tuple[str, int]:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            digest = hashlib.sha256()
            total = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise ValueError("private file exceeds byte bound")
                digest.update(chunk)
            return digest.hexdigest(), total
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_private_bytes(path: Path, payload: bytes, maximum: int) -> None:
        if not payload or len(payload) > maximum:
            raise ValueError("generated output is empty or exceeds its byte bound")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            written = 0
            view = memoryview(payload)
            while written < len(payload):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise OSError("short write while publishing ReelBench output")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @staticmethod
    def _reject_unknown(options: Mapping[str, Any], allowed: set[str]) -> None:
        if set(options) - allowed:
            raise ValueError("unsupported ReelBench action options")

    @staticmethod
    def _remove_empty_output_root(path: Path) -> None:
        try:
            for child in path.iterdir():
                if child.is_file() or child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    for nested in child.iterdir():
                        if nested.is_file() or nested.is_symlink():
                            nested.unlink()
                    child.rmdir()
            path.rmdir()
        except OSError:
            pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = ["ReelBenchProjectService"]
