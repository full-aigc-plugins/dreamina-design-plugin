"""Verify the pinned ReelBench Skill snapshot and its sole alias delta.

This verifier is independent from ``verify_skill_snapshot.py`` so the original
Dreamina upstream-snapshot contract remains unchanged.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PINNED_REVISION = "18f2f63987337df0975a89973d38d50f3231ee31"
SOURCE_URL = "https://github.com/eternityspring/reelbench-skills.git"
ALIASES = {
    "video-shots": "dreamina-video-shots",
    "video-sync": "dreamina-video-sync",
}
LOCK_TOP_LEVEL_KEYS = {"schema_version", "source", "revision", "aliases", "files"}
LOCK_FILE_KEYS = {"git_blob", "upstream_sha256", "packaged_sha256"}
GIT_BLOB_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_LOCK_BYTES = 4 * 1024 * 1024
MAX_SNAPSHOT_FILE_BYTES = 64 * 1024 * 1024


class DuplicateJsonKeyError(ValueError):
    """Raised when a supposedly closed lock JSON object repeats a key."""


@dataclass(frozen=True)
class ReelBenchSnapshotReport:
    """Deterministic result for local bytes and optional source provenance."""

    revision: str
    packaged_names: list[str]
    mismatches: list[str] = field(default_factory=list)
    source_status: str = "PARTIAL"
    source_reason: str = ""

    def to_json(self) -> str:
        """Serialize the report for CI without relying on text diagnostics."""
        return json.dumps(dataclasses.asdict(self), indent=2, sort_keys=True)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _is_directory(path: Path) -> bool:
    metadata = _lstat(path)
    return metadata is not None and stat.S_ISDIR(metadata.st_mode)


def _read_regular_file(path: Path, max_bytes: int) -> bytes | None:
    """Read one bounded regular file without following a replaced symlink."""
    before = _lstat(path)
    if (
        before is None
        or not stat.S_ISREG(before.st_mode)
        or before.st_size < 0
        or before.st_size > max_bytes
    ):
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_size > max_bytes
        ):
            return None
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
        ):
            return None
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _collect_regular_files(root: Path, prefix: str) -> tuple[dict[str, bytes], list[str]]:
    """Collect only regular files while rejecting every symlink/special entry."""
    metadata = _lstat(root)
    if metadata is None or not stat.S_ISDIR(metadata.st_mode):
        return {}, [prefix]

    files: dict[str, bytes] = {}
    diagnostics: list[str] = []

    def visit(directory: Path, relative: Path) -> None:
        directory_metadata = _lstat(directory)
        if directory_metadata is None or not stat.S_ISDIR(directory_metadata.st_mode):
            diagnostics.append((Path(prefix) / relative).as_posix())
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            diagnostics.append((Path(prefix) / relative).as_posix())
            return
        for entry in entries:
            child = Path(entry.path)
            child_relative = relative / entry.name
            display = (Path(prefix) / child_relative).as_posix()
            child_metadata = _lstat(child)
            if child_metadata is None:
                diagnostics.append(display)
                continue
            if stat.S_ISDIR(child_metadata.st_mode):
                visit(child, child_relative)
                continue
            if not stat.S_ISREG(child_metadata.st_mode):
                diagnostics.append(display)
                continue
            data = _read_regular_file(child, MAX_SNAPSHOT_FILE_BYTES)
            if data is None:
                diagnostics.append(display)
                continue
            files[(Path(prefix) / child_relative).as_posix()] = data

    visit(root, Path())
    return files, diagnostics


def _safe_lock_file(plugin_root: Path) -> tuple[Path | None, list[str]]:
    upstream_directory = plugin_root / "upstream"
    upstream_metadata = _lstat(upstream_directory)
    if upstream_metadata is None or not stat.S_ISDIR(upstream_metadata.st_mode):
        return None, ["upstream"]
    lock_path = upstream_directory / "reelbench.lock.json"
    lock_metadata = _lstat(lock_path)
    if lock_metadata is None or not stat.S_ISREG(lock_metadata.st_mode):
        return None, ["upstream/reelbench.lock.json"]
    return lock_path, []


def _safe_lock_key(key: object) -> bool:
    if not isinstance(key, str) or key.startswith("/") or "\\" in key or "\x00" in key:
        return False
    pieces = key.split("/")
    return (
        len(pieces) >= 2
        and pieces[0] in ALIASES
        and all(piece not in {"", ".", ".."} for piece in pieces)
    )


def _read_lock(plugin_root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    lock_path, path_errors = _safe_lock_file(plugin_root)
    if lock_path is None:
        return None, path_errors
    lock_bytes = _read_regular_file(lock_path, MAX_LOCK_BYTES)
    if lock_bytes is None:
        return None, ["upstream/reelbench.lock.json"]
    try:
        lock = json.loads(
            lock_bytes.decode("utf-8"),
            object_pairs_hook=_duplicate_rejecting_object,
        )
    except DuplicateJsonKeyError as error:
        return None, [f"lock:duplicate-key:{error}"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, ["upstream/reelbench.lock.json"]
    if not isinstance(lock, dict) or set(lock) != LOCK_TOP_LEVEL_KEYS:
        return None, ["lock:top-level"]
    errors: list[str] = []
    if lock.get("schema_version") != "1.0.0":
        errors.append("lock:schema_version")
    if lock.get("source") != SOURCE_URL:
        errors.append("lock:source")
    if lock.get("revision") != PINNED_REVISION:
        errors.append("lock:revision")
    if lock.get("aliases") != ALIASES:
        errors.append("lock:aliases")
    files = lock.get("files")
    if not isinstance(files, dict) or not files:
        errors.append("lock:files")
    else:
        normalized: set[tuple[str, str]] = set()
        for key in sorted(files, key=str):
            if not _safe_lock_key(key):
                errors.append(f"lock:files/{key}")
                continue
            upstream_name, relative = key.split("/", 1)
            identity = (upstream_name, relative)
            if identity in normalized:
                errors.append(f"lock:collision:{key}")
            normalized.add(identity)
            entry = files[key]
            if not isinstance(entry, dict) or set(entry) != LOCK_FILE_KEYS:
                errors.append(f"lock:files/{key}")
                continue
            if (
                not isinstance(entry.get("git_blob"), str)
                or GIT_BLOB_RE.fullmatch(entry["git_blob"]) is None
                or not isinstance(entry.get("upstream_sha256"), str)
                or SHA256_RE.fullmatch(entry["upstream_sha256"]) is None
                or not isinstance(entry.get("packaged_sha256"), str)
                or SHA256_RE.fullmatch(entry["packaged_sha256"]) is None
            ):
                errors.append(f"lock:files/{key}")
    return (None, sorted(set(errors))) if errors else (lock, [])


def _skill_alias_bytes(source: bytes, upstream_name: str, packaged_name: str) -> bytes:
    """Apply precisely one LF-delimited frontmatter-name replacement."""
    if not source.startswith(b"---\n"):
        raise ValueError("frontmatter opening is not exact")
    closing = source.find(b"\n---\n", 4)
    if closing < 0:
        raise ValueError("frontmatter closing is not exact")
    frontmatter = source[4:closing].splitlines(keepends=True)
    expected = f"name: {upstream_name}\n".encode("utf-8")
    replacement = f"name: {packaged_name}\n".encode("utf-8")
    indexes = [index for index, line in enumerate(frontmatter) if line == expected]
    if len(indexes) != 1:
        raise ValueError("frontmatter name line is not exact")
    frontmatter[indexes[0]] = replacement
    return b"---\n" + b"".join(frontmatter) + source[closing:]


def _packaged_frontmatter_is_exact(packaged: bytes, packaged_name: str) -> bool:
    if not packaged.startswith(b"---\n"):
        return False
    closing = packaged.find(b"\n---\n", 4)
    if closing < 0:
        return False
    return packaged[4:closing].splitlines(keepends=True).count(
        f"name: {packaged_name}\n".encode("utf-8")
    ) == 1


def _resolve_upstream_skills_root(upstream_root: Path) -> Path:
    skills_root = upstream_root / "skills"
    return skills_root if _is_directory(skills_root) else upstream_root


def _run_git(upstream_root: Path, args: list[str], *, text: bool = False) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        ["git", "-C", str(upstream_root), *args],
        check=False,
        capture_output=True,
        text=text,
    )


def _canonical_origin(url: str) -> bool:
    return url.strip().rstrip("/").removesuffix(".git") == SOURCE_URL.removesuffix(".git")


def _git_snapshot_files(upstream_root: Path) -> tuple[dict[str, tuple[str, bytes]] | None, list[str]]:
    """Read bytes only from the pinned commit after validating Git provenance."""
    is_git = _run_git(upstream_root, ["rev-parse", "--is-inside-work-tree"], text=True)
    if is_git.returncode != 0 or is_git.stdout.strip() != "true":
        return None, []
    origin = _run_git(upstream_root, ["config", "--get", "remote.origin.url"], text=True)
    if origin.returncode != 0 or not _canonical_origin(origin.stdout):
        return None, ["upstream.origin"]
    revision = _run_git(upstream_root, ["rev-parse", "--verify", f"{PINNED_REVISION}^{{commit}}"], text=True)
    if revision.returncode != 0 or revision.stdout.strip() != PINNED_REVISION:
        return None, ["upstream.revision"]
    head = _run_git(upstream_root, ["rev-parse", "HEAD"], text=True)
    if head.returncode != 0 or head.stdout.strip() != PINNED_REVISION:
        return None, ["upstream.HEAD"]
    listed = _run_git(
        upstream_root,
        [
            "ls-tree",
            "-r",
            "--long",
            PINNED_REVISION,
            "--",
            "skills/video-shots",
            "skills/video-sync",
        ],
        text=True,
    )
    if listed.returncode != 0:
        return None, ["upstream.revision"]
    files: dict[str, tuple[str, bytes]] = {}
    for line in listed.stdout.splitlines():
        try:
            metadata, git_path = line.split("\t", 1)
            mode, kind, git_blob, _ = metadata.split()
            parts = Path(git_path).relative_to("skills").parts
            if mode not in {"100644", "100755"} or kind != "blob" or len(parts) < 2:
                return None, ["upstream.tree"]
            upstream_name = parts[0]
            relative = Path(*parts[1:]).as_posix()
            content = _run_git(upstream_root, ["show", f"{PINNED_REVISION}:{git_path}"])
            if content.returncode != 0:
                return None, ["upstream.revision"]
            files[f"{upstream_name}/{relative}"] = (git_blob, content.stdout)
        except (ValueError, OSError):
            return None, ["upstream.tree"]
    return files, []


def _filesystem_snapshot_files(upstream_root: Path) -> tuple[dict[str, tuple[str, bytes]], list[str]]:
    skills_root = _resolve_upstream_skills_root(upstream_root)
    files: dict[str, tuple[str, bytes]] = {}
    diagnostics: list[str] = []
    for upstream_name in ALIASES:
        tree, tree_errors = _collect_regular_files(skills_root / upstream_name, upstream_name)
        diagnostics.extend(tree_errors)
        for key, source in tree.items():
            files[key] = (_git_blob(source), source)
    return files, diagnostics


def verify_reelbench_snapshot(
    plugin_root: Path, upstream_root: Path | None = None
) -> ReelBenchSnapshotReport:
    """Verify packaged bytes and, when possible, provenance from the pinned Git tree."""
    root = Path(plugin_root)
    root_metadata = _lstat(root)
    if root_metadata is None or not stat.S_ISDIR(root_metadata.st_mode):
        return ReelBenchSnapshotReport(
            revision=PINNED_REVISION,
            packaged_names=[],
            mismatches=["plugin_root"],
            source_status="UNVERIFIABLE",
            source_reason="plugin_root is missing, a symlink, or not a directory",
        )
    lock, mismatches = _read_lock(root)
    if lock is None:
        return ReelBenchSnapshotReport(
            revision=PINNED_REVISION,
            packaged_names=[],
            mismatches=sorted(set(mismatches)),
            source_status="INVALID_LOCK",
            source_reason="lock manifest is missing, unsafe, malformed, or invalid",
        )

    lock_files = lock["files"]
    assert isinstance(lock_files, dict)
    expected_keys = set(lock_files)
    packaged_names: list[str] = []
    local_files: dict[str, bytes] = {}
    skills_metadata = _lstat(root / "skills")
    if skills_metadata is None or not stat.S_ISDIR(skills_metadata.st_mode):
        mismatches.append("skills")
    else:
        for upstream_name, packaged_name in ALIASES.items():
            tree, tree_errors = _collect_regular_files(root / "skills" / packaged_name, upstream_name)
            local_files.update(tree)
            mismatches.extend(tree_errors)
            skill_key = f"{upstream_name}/SKILL.md"
            if skill_key in tree and _packaged_frontmatter_is_exact(tree[skill_key], packaged_name):
                packaged_names.append(packaged_name)
            else:
                mismatches.append(skill_key)

    for key in sorted(set(local_files) ^ expected_keys):
        mismatches.append(key)
    for key in sorted(expected_keys & set(local_files)):
        entry = lock_files[key]
        assert isinstance(entry, dict)
        if _sha256(local_files[key]) != entry["packaged_sha256"]:
            mismatches.append(key)

    source_status = "PARTIAL"
    source_reason = "upstream_root not provided; local bytes match the closed lock only"
    if upstream_root is not None:
        supplied_root = Path(upstream_root)
        supplied_metadata = _lstat(supplied_root)
        if supplied_metadata is None or not stat.S_ISDIR(supplied_metadata.st_mode):
            mismatches.append("upstream_root")
            source_status = "UNVERIFIABLE"
            source_reason = "upstream_root is missing, a symlink, or not a directory"
        else:
            source_files, git_errors = _git_snapshot_files(supplied_root)
            if git_errors:
                mismatches.extend(git_errors)
                source_status = "UNVERIFIABLE"
                source_reason = "pinned Git provenance could not be verified"
            elif source_files is not None:
                source_status = "PINNED_GIT"
                source_reason = "origin, HEAD, and source bytes match the pinned Git tree"
            else:
                source_files, filesystem_errors = _filesystem_snapshot_files(supplied_root)
                mismatches.extend(filesystem_errors)
                source_status = "PARTIAL"
                source_reason = "source bytes were compared from a non-Git directory"
            if source_files is not None:
                for key in sorted(set(source_files) ^ expected_keys):
                    mismatches.append(key)
                for key in sorted(expected_keys & set(source_files)):
                    entry = lock_files[key]
                    assert isinstance(entry, dict)
                    git_blob, source_bytes = source_files[key]
                    upstream_name, relative = key.split("/", 1)
                    try:
                        expected_packaged = (
                            _skill_alias_bytes(source_bytes, upstream_name, ALIASES[upstream_name])
                            if relative == "SKILL.md"
                            else source_bytes
                        )
                    except ValueError:
                        mismatches.append(key)
                        continue
                    if (
                        git_blob != entry["git_blob"]
                        or _sha256(source_bytes) != entry["upstream_sha256"]
                        or _sha256(expected_packaged) != entry["packaged_sha256"]
                        or local_files.get(key) != expected_packaged
                    ):
                        mismatches.append(key)

    return ReelBenchSnapshotReport(
        revision=PINNED_REVISION,
        packaged_names=sorted(packaged_names),
        mismatches=sorted(set(mismatches)),
        source_status=source_status,
        source_reason=source_reason,
    )


def main(argv: list[str] | None = None) -> int:
    """Emit a JSON report; ``--strict`` requires pinned Git provenance."""
    args = list(sys.argv[1:] if argv is None else argv)
    plugin_root = Path.cwd()
    upstream_root: Path | None = None
    strict = False
    allow_partial = False
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--plugin-root" and index + 1 < len(args):
            plugin_root = Path(args[index + 1])
            index += 2
        elif argument == "--upstream-root" and index + 1 < len(args):
            upstream_root = Path(args[index + 1])
            index += 2
        elif argument in {"--strict", "--strict-pinned-source"}:
            strict = True
            index += 1
        elif argument == "--allow-partial":
            allow_partial = True
            index += 1
        else:
            print(json.dumps({"error": f"invalid argument: {argument}"}, sort_keys=True))
            return 2
    if strict and allow_partial:
        print(json.dumps({"error": "--allow-partial conflicts with strict mode"}, sort_keys=True))
        return 2
    report = verify_reelbench_snapshot(plugin_root, upstream_root)
    print(report.to_json())
    if report.mismatches:
        return 2
    if strict and report.source_status != "PINNED_GIT":
        return 1
    if report.source_status == "PARTIAL" and not allow_partial:
        return 1
    return 0


__all__ = [
    "ALIASES",
    "PINNED_REVISION",
    "ReelBenchSnapshotReport",
    "SOURCE_URL",
    "main",
    "verify_reelbench_snapshot",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
