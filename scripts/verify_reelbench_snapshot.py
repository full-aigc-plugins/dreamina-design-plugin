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
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Windows does not provide fcntl; safe opening then fails closed below.
    import fcntl
except ImportError:  # pragma: no cover - platform-dependent safety boundary
    fcntl = None  # type: ignore[assignment]


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


@dataclass
class DirectoryHandle:
    """One opened directory and its stable identity within an FD-rooted walk."""

    fd: int
    dev: int
    ino: int
    parent_fd: int | None
    name: str | None
    display: str


@dataclass
class DirectoryPath:
    """Retained FD chain from an anchor directory to a caller-supplied root."""

    handles: list[DirectoryHandle]

    @property
    def root(self) -> DirectoryHandle:
        return self.handles[-1]

    def close(self) -> None:
        for handle in reversed(self.handles):
            try:
                os.close(handle.fd)
            except OSError:
                pass
            if handle.parent_fd is not None:
                try:
                    os.close(handle.parent_fd)
                except OSError:
                    pass


def _missing_safe_open_flags() -> list[str]:
    return [
        name for name in ("O_DIRECTORY", "O_NOFOLLOW")
        if not isinstance(getattr(os, name, None), int)
    ]


def _safe_open_supported() -> bool:
    return not _missing_safe_open_flags()


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size


def _open_directory_at(parent_fd: int, name: str, display: str) -> DirectoryHandle | None:
    """Open a child directory from a validated parent FD without following links."""
    descriptor: int | None = None
    retained_parent: int | None = None
    handle: DirectoryHandle | None = None
    try:
        before = os.lstat(name, dir_fd=parent_fd)
        if not stat.S_ISDIR(before.st_mode):
            return None
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or _identity(opened) != _identity(before):
            return None
        retained_parent = os.dup(parent_fd)
        handle = DirectoryHandle(
            fd=descriptor,
            dev=opened.st_dev,
            ino=opened.st_ino,
            parent_fd=retained_parent,
            name=name,
            display=display,
        )
        return handle
    except OSError:
        return None
    finally:
        # Transfer ownership only after the complete handle exists. Even a
        # non-OSError during construction must release both newly acquired FDs.
        if handle is None:
            try:
                if retained_parent is not None:
                    os.close(retained_parent)
            finally:
                if descriptor is not None:
                    os.close(descriptor)


def _open_directory_path(path: Path, display: str) -> DirectoryPath | None:
    """Open a raw caller path component-by-component from a trusted anchor FD."""
    if not _safe_open_supported():
        return None
    raw = Path(path)
    parts = raw.parts
    absolute = raw.is_absolute()
    components = list(parts[1:] if absolute else parts)
    if any(component in {"", ".", ".."} for component in components):
        return None
    try:
        with ExitStack() as cleanup:
            anchor_fd = os.open("/" if absolute else ".", _directory_flags())
            cleanup.callback(os.close, anchor_fd)
            anchor = os.fstat(anchor_fd)
            if not stat.S_ISDIR(anchor.st_mode):
                return None
            handles = [
                DirectoryHandle(
                    fd=anchor_fd,
                    dev=anchor.st_dev,
                    ino=anchor.st_ino,
                    parent_fd=None,
                    name=None,
                    display=display,
                )
            ]
            for component in components:
                child = _open_directory_at(handles[-1].fd, component, display)
                if child is None:
                    return None
                cleanup.callback(_close_handle, child)
                handles.append(child)
            directory_path = DirectoryPath(handles)
            # The returned path owns the complete chain only after construction.
            cleanup.pop_all()
            return directory_path
    except OSError:
        return None


def _handle_is_current(handle: DirectoryHandle) -> bool:
    try:
        opened = os.fstat(handle.fd)
        if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (handle.dev, handle.ino):
            return False
        if handle.parent_fd is None or handle.name is None:
            return True
        current = _open_directory_at(handle.parent_fd, handle.name, handle.display)
        if current is None:
            return False
        try:
            return (current.dev, current.ino) == (handle.dev, handle.ino)
        finally:
            os.close(current.fd)
            if current.parent_fd is not None:
                os.close(current.parent_fd)
    except OSError:
        return False


def _path_is_current(path: DirectoryPath) -> bool:
    return all(_handle_is_current(handle) for handle in path.handles)


def _read_regular_at(parent_fd: int, name: str, max_bytes: int) -> bytes | None:
    """Read one bounded regular child through its validated parent directory FD."""
    try:
        before = os.lstat(name, dir_fd=parent_fd)
    except OSError:
        return None
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size < 0
        or before.st_size > max_bytes
    ):
        return None
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
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
        current = os.lstat(name, dir_fd=parent_fd)
        if _identity(current) != _identity(before) or not stat.S_ISREG(current.st_mode):
            return None
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _close_handle(handle: DirectoryHandle) -> None:
    try:
        os.close(handle.fd)
    finally:
        if handle.parent_fd is not None:
            os.close(handle.parent_fd)


def _collect_regular_files(root: DirectoryHandle, prefix: str) -> tuple[dict[str, bytes], list[str]]:
    """Collect only regular files while rejecting every symlink/special entry."""
    files: dict[str, bytes] = {}
    diagnostics: list[str] = []

    def visit(directory: DirectoryHandle, relative: Path) -> None:
        if not _handle_is_current(directory):
            diagnostics.append((Path(prefix) / relative).as_posix())
            return
        try:
            entries = sorted(os.listdir(directory.fd))
        except OSError:
            diagnostics.append((Path(prefix) / relative).as_posix())
            return
        for entry in entries:
            child_relative = relative / entry
            display = (Path(prefix) / child_relative).as_posix()
            try:
                child_metadata = os.lstat(entry, dir_fd=directory.fd)
            except OSError:
                diagnostics.append(display)
                continue
            if stat.S_ISDIR(child_metadata.st_mode):
                child = _open_directory_at(directory.fd, entry, display)
                if child is None:
                    diagnostics.append(display)
                    continue
                try:
                    visit(child, child_relative)
                    if not _handle_is_current(child):
                        diagnostics.append(display)
                finally:
                    _close_handle(child)
                continue
            if not stat.S_ISREG(child_metadata.st_mode):
                diagnostics.append(display)
                continue
            data = _read_regular_at(directory.fd, entry, MAX_SNAPSHOT_FILE_BYTES)
            if data is None:
                diagnostics.append(display)
                continue
            files[(Path(prefix) / child_relative).as_posix()] = data

    visit(root, Path())
    return files, diagnostics


def _safe_lock_key(key: object) -> bool:
    if not isinstance(key, str) or key.startswith("/") or "\\" in key or "\x00" in key:
        return False
    pieces = key.split("/")
    return (
        len(pieces) >= 2
        and pieces[0] in ALIASES
        and all(piece not in {"", ".", ".."} for piece in pieces)
    )


def _read_lock(plugin_root: DirectoryHandle) -> tuple[dict[str, Any] | None, list[str]]:
    upstream_directory = _open_directory_at(plugin_root.fd, "upstream", "upstream")
    if upstream_directory is None:
        return None, ["upstream"]
    try:
        lock_bytes = _read_regular_at(
            upstream_directory.fd,
            "reelbench.lock.json",
            MAX_LOCK_BYTES,
        )
        if lock_bytes is None or not _handle_is_current(upstream_directory):
            return None, ["upstream/reelbench.lock.json"]
    finally:
        _close_handle(upstream_directory)
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


def _descriptor_workdir(handle: DirectoryHandle) -> tuple[str, tuple[int, ...]] | None:
    """Produce a Git workdir from an already-open directory, never raw input."""
    if fcntl is not None and hasattr(fcntl, "F_GETPATH"):
        try:
            raw = fcntl.fcntl(handle.fd, fcntl.F_GETPATH, b"\0" * 1024)
            if isinstance(raw, bytes):
                path = raw.split(b"\0", 1)[0].decode("utf-8")
                if path:
                    return path, ()
        except OSError:
            pass
    descriptor_path = f"/proc/self/fd/{handle.fd}"
    if os.path.isdir("/proc/self/fd"):
        return descriptor_path, (handle.fd,)
    return None


def _run_git(
    workdir: str,
    pass_fds: tuple[int, ...],
    args: list[str],
    *,
    text: bool = False,
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        ["git", "-C", workdir, *args],
        check=False,
        capture_output=True,
        text=text,
        pass_fds=pass_fds,
    )


def _canonical_origin(url: str) -> bool:
    return url.strip().rstrip("/").removesuffix(".git") == SOURCE_URL.removesuffix(".git")


def _git_snapshot_files(upstream_root: DirectoryHandle) -> tuple[dict[str, tuple[str, bytes]] | None, list[str]]:
    """Read bytes only from the pinned commit after validating Git provenance."""
    descriptor_workdir = _descriptor_workdir(upstream_root)
    if descriptor_workdir is None:
        return None, ["runtime.descriptor_path"]
    workdir, pass_fds = descriptor_workdir
    is_git = _run_git(workdir, pass_fds, ["rev-parse", "--is-inside-work-tree"], text=True)
    if is_git.returncode != 0 or is_git.stdout.strip() != "true":
        return None, []
    origin = _run_git(workdir, pass_fds, ["config", "--get", "remote.origin.url"], text=True)
    if origin.returncode != 0 or not _canonical_origin(origin.stdout):
        return None, ["upstream.origin"]
    revision = _run_git(workdir, pass_fds, ["rev-parse", "--verify", f"{PINNED_REVISION}^{{commit}}"], text=True)
    if revision.returncode != 0 or revision.stdout.strip() != PINNED_REVISION:
        return None, ["upstream.revision"]
    head = _run_git(workdir, pass_fds, ["rev-parse", "HEAD"], text=True)
    if head.returncode != 0 or head.stdout.strip() != PINNED_REVISION:
        return None, ["upstream.HEAD"]
    listed = _run_git(
        workdir,
        pass_fds,
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
            content = _run_git(workdir, pass_fds, ["show", f"{PINNED_REVISION}:{git_path}"])
            if content.returncode != 0:
                return None, ["upstream.revision"]
            files[f"{upstream_name}/{relative}"] = (git_blob, content.stdout)
        except (ValueError, OSError):
            return None, ["upstream.tree"]
    return files, []


def _filesystem_snapshot_files(upstream_root: DirectoryHandle) -> tuple[dict[str, tuple[str, bytes]], list[str]]:
    files: dict[str, tuple[str, bytes]] = {}
    diagnostics: list[str] = []
    skills_root = upstream_root
    owns_skills_root = False
    try:
        skills_metadata = os.lstat("skills", dir_fd=upstream_root.fd)
    except OSError:
        skills_metadata = None
    if skills_metadata is not None:
        if not stat.S_ISDIR(skills_metadata.st_mode):
            return {}, ["upstream/skills"]
        opened_skills = _open_directory_at(upstream_root.fd, "skills", "upstream/skills")
        if opened_skills is None:
            return {}, ["upstream/skills"]
        skills_root = opened_skills
        owns_skills_root = True
    try:
        for upstream_name in ALIASES:
            directory = _open_directory_at(skills_root.fd, upstream_name, upstream_name)
            if directory is None:
                diagnostics.append(upstream_name)
                continue
            try:
                tree, tree_errors = _collect_regular_files(directory, upstream_name)
                diagnostics.extend(tree_errors)
                if not _handle_is_current(directory):
                    diagnostics.append(upstream_name)
                for key, source in tree.items():
                    files[key] = (_git_blob(source), source)
            finally:
                _close_handle(directory)
        if owns_skills_root:
            if not _handle_is_current(skills_root):
                diagnostics.append("upstream/skills")
        return files, diagnostics
    finally:
        if owns_skills_root:
            _close_handle(skills_root)


def verify_reelbench_snapshot(
    plugin_root: Path, upstream_root: Path | None = None
) -> ReelBenchSnapshotReport:
    """Verify packaged bytes and, when possible, provenance from the pinned Git tree."""
    missing_flags = _missing_safe_open_flags()
    if missing_flags:
        return ReelBenchSnapshotReport(
            revision=PINNED_REVISION,
            packaged_names=[],
            mismatches=[f"runtime.{name}" for name in missing_flags],
            source_status="UNVERIFIABLE",
            source_reason="the platform cannot safely open untrusted paths without " + ", ".join(missing_flags),
        )
    plugin_path = _open_directory_path(Path(plugin_root), "plugin_root")
    if plugin_path is None:
        return ReelBenchSnapshotReport(
            revision=PINNED_REVISION,
            packaged_names=[],
            mismatches=["plugin_root"],
            source_status="UNVERIFIABLE",
            source_reason="plugin_root is missing, a symlink, or not a directory",
        )
    # Own the root chain across every return and unexpected exception below.
    try:
        lock, mismatches = _read_lock(plugin_path.root)
        if lock is None:
            if not _path_is_current(plugin_path):
                mismatches.append("plugin_root")
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
        skills_directory = _open_directory_at(plugin_path.root.fd, "skills", "skills")
        if skills_directory is None:
            mismatches.append("skills")
        else:
            try:
                for upstream_name, packaged_name in ALIASES.items():
                    skill_directory = _open_directory_at(
                        skills_directory.fd,
                        packaged_name,
                        upstream_name,
                    )
                    if skill_directory is None:
                        mismatches.append(upstream_name)
                        continue
                    try:
                        tree, tree_errors = _collect_regular_files(skill_directory, upstream_name)
                        local_files.update(tree)
                        mismatches.extend(tree_errors)
                        if not _handle_is_current(skill_directory):
                            mismatches.append(upstream_name)
                        skill_key = f"{upstream_name}/SKILL.md"
                        if skill_key in tree and _packaged_frontmatter_is_exact(tree[skill_key], packaged_name):
                            packaged_names.append(packaged_name)
                        else:
                            mismatches.append(skill_key)
                    finally:
                        _close_handle(skill_directory)
                if not _handle_is_current(skills_directory):
                    mismatches.append("skills")
            finally:
                _close_handle(skills_directory)

        for key in sorted(set(local_files) ^ expected_keys):
            mismatches.append(key)
        for key in sorted(expected_keys & set(local_files)):
            entry = lock_files[key]
            assert isinstance(entry, dict)
            if _sha256(local_files[key]) != entry["packaged_sha256"]:
                mismatches.append(key)

        source_status = "PARTIAL"
        source_reason = "upstream_root not provided; pinned Git provenance was not checked"
        if upstream_root is not None:
            upstream_path = _open_directory_path(Path(upstream_root), "upstream_root")
            if upstream_path is None:
                mismatches.append("upstream_root")
                source_status = "UNVERIFIABLE"
                source_reason = "upstream_root is missing, a symlink, or not a directory"
            else:
                try:
                    source_files, git_errors = _git_snapshot_files(upstream_path.root)
                    if git_errors:
                        mismatches.extend(git_errors)
                        source_status = "UNVERIFIABLE"
                        source_reason = "pinned Git provenance could not be verified"
                    elif source_files is not None:
                        source_status = "PINNED_GIT"
                        source_reason = "origin, HEAD, and the pinned Git tree are available"
                    else:
                        source_files, filesystem_errors = _filesystem_snapshot_files(upstream_path.root)
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
                    if not _path_is_current(upstream_path):
                        mismatches.append("upstream_root")
                        source_status = "UNVERIFIABLE"
                        source_reason = "upstream_root changed after initial validation"
                finally:
                    upstream_path.close()

        if not _path_is_current(plugin_path):
            mismatches.append("plugin_root")
        if source_status == "PINNED_GIT" and mismatches:
            source_reason = "pinned Git source was available; snapshot comparison reported mismatches"

        return ReelBenchSnapshotReport(
            revision=PINNED_REVISION,
            packaged_names=sorted(packaged_names),
            mismatches=sorted(set(mismatches)),
            source_status=source_status,
            source_reason=source_reason,
        )
    finally:
        plugin_path.close()


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
