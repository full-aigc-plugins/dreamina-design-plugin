"""Verify the exact pinned ReelBench Skill snapshot and its two aliases.

This verifier is deliberately separate from ``verify_skill_snapshot.py``: the
existing Dreamina upstream snapshot remains governed by its original contract.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


PINNED_REVISION = "18f2f63987337df0975a89973d38d50f3231ee31"
SOURCE_URL = "https://github.com/eternityspring/reelbench-skills.git"
ALIASES = {
    "video-shots": "dreamina-video-shots",
    "video-sync": "dreamina-video-sync",
}
LOCK_TOP_LEVEL_KEYS = {"schema_version", "source", "revision", "aliases", "files"}
LOCK_FILE_KEYS = {"git_blob", "upstream_sha256", "packaged_sha256"}


@dataclass(frozen=True)
class ReelBenchSnapshotReport:
    """The deterministic result of checking a packaged ReelBench snapshot."""

    revision: str
    packaged_names: list[str]
    mismatches: list[str] = field(default_factory=list)
    source_status: str = "LOCK_ONLY"
    source_reason: str = ""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _skill_alias_bytes(source: bytes, upstream_name: str, packaged_name: str) -> bytes:
    """Apply precisely the sole permitted frontmatter-name transformation."""
    if not source.startswith(b"---\n"):
        raise ValueError("SKILL.md lacks an LF-delimited frontmatter opening")
    closing = source.find(b"\n---\n", 4)
    if closing < 0:
        raise ValueError("SKILL.md lacks an LF-delimited frontmatter closing")
    frontmatter = source[4:closing].splitlines(keepends=True)
    expected = f"name: {upstream_name}\n".encode("utf-8")
    replacement = f"name: {packaged_name}\n".encode("utf-8")
    matches = [index for index, line in enumerate(frontmatter) if line == expected]
    if len(matches) != 1:
        raise ValueError("SKILL.md must contain exactly one unchanged name frontmatter line")
    frontmatter[matches[0]] = replacement
    return b"---\n" + b"".join(frontmatter) + source[closing:]


def _resolve_upstream_skills_root(upstream_root: Path) -> Path:
    skills_root = upstream_root / "skills"
    return skills_root if skills_root.is_dir() else upstream_root


def _read_lock(plugin_root: Path) -> tuple[dict[str, object] | None, list[str]]:
    lock_path = plugin_root / "upstream" / "reelbench.lock.json"
    if not lock_path.is_file():
        return None, ["upstream/reelbench.lock.json"]
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, ["upstream/reelbench.lock.json"]
    if not isinstance(lock, dict) or set(lock) != LOCK_TOP_LEVEL_KEYS:
        return None, ["upstream/reelbench.lock.json"]
    if (
        lock.get("schema_version") != "1.0.0"
        or lock.get("source") != SOURCE_URL
        or lock.get("revision") != PINNED_REVISION
        or lock.get("aliases") != ALIASES
        or not isinstance(lock.get("files"), dict)
    ):
        return None, ["upstream/reelbench.lock.json"]
    return lock, []


def _git_snapshot_files(upstream_root: Path) -> dict[str, tuple[str, bytes]] | None:
    """Read the pinned Git tree when the supplied source is a checkout."""
    if not (upstream_root / ".git").exists():
        return None
    listed = subprocess.run(
        [
            "git",
            "-C",
            str(upstream_root),
            "ls-tree",
            "-r",
            "--long",
            PINNED_REVISION,
            "--",
            "skills/video-shots",
            "skills/video-sync",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        return {}
    files: dict[str, tuple[str, bytes]] = {}
    for line in listed.stdout.splitlines():
        metadata, git_path = line.split("\t", 1)
        _, _, git_blob, _ = metadata.split()
        upstream_name, relative = Path(git_path).relative_to("skills").parts[0], Path(git_path).relative_to("skills").parts[1:]
        content = subprocess.run(
            ["git", "-C", str(upstream_root), "show", f"{PINNED_REVISION}:{git_path}"],
            check=False,
            capture_output=True,
        )
        if content.returncode != 0:
            return {}
        files[f"{upstream_name}/{Path(*relative).as_posix()}"] = (git_blob, content.stdout)
    return files


def _filesystem_snapshot_files(upstream_root: Path) -> dict[str, tuple[str, bytes]]:
    skills_root = _resolve_upstream_skills_root(upstream_root)
    files: dict[str, tuple[str, bytes]] = {}
    for upstream_name in ALIASES:
        directory = skills_root / upstream_name
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.rglob("*")):
            if candidate.is_file():
                data = candidate.read_bytes()
                relative = candidate.relative_to(directory).as_posix()
                files[f"{upstream_name}/{relative}"] = (_git_blob(data), data)
    return files


def verify_reelbench_snapshot(
    plugin_root: Path, upstream_root: Path | None = None
) -> ReelBenchSnapshotReport:
    """Verify both packaged Skills against the lock and optionally source bytes.

    With a Git checkout, source bytes are read directly from the pinned commit,
    so a changed working tree cannot influence the result. A non-Git directory
    is accepted for hermetic tests and checked with the same byte/blob rules.
    """
    root = Path(plugin_root)
    lock, mismatches = _read_lock(root)
    if lock is None:
        return ReelBenchSnapshotReport(
            revision=PINNED_REVISION,
            packaged_names=[],
            mismatches=sorted(set(mismatches)),
            source_status="INVALID_LOCK",
            source_reason="lock manifest is missing, malformed, or violates the closed contract",
        )

    files = lock["files"]
    assert isinstance(files, dict)
    expected_keys = set(files)
    packaged_names: list[str] = []
    local_files: dict[str, bytes] = {}
    for upstream_name, packaged_name in ALIASES.items():
        packaged_dir = root / "skills" / packaged_name
        skill_md = packaged_dir / "SKILL.md"
        if not skill_md.is_file():
            mismatches.append(f"{upstream_name}/SKILL.md")
            continue
        try:
            declared_name = next(
                line.split(":", 1)[1].strip()
                for line in skill_md.read_text(encoding="utf-8").splitlines()
                if line.startswith("name:")
            )
        except (OSError, StopIteration, UnicodeDecodeError):
            mismatches.append("SKILL.md")
            continue
        if declared_name != packaged_name:
            mismatches.append("SKILL.md")
        packaged_names.append(packaged_name)
        for candidate in sorted(packaged_dir.rglob("*")):
            if candidate.is_file():
                relative = candidate.relative_to(packaged_dir).as_posix()
                local_files[f"{upstream_name}/{relative}"] = candidate.read_bytes()

    for key in sorted(set(local_files) - expected_keys):
        mismatches.append(key.split("/", 1)[1])
    for key in sorted(expected_keys - set(local_files)):
        mismatches.append(key.split("/", 1)[1])
    for key in sorted(expected_keys & set(local_files)):
        entry = files[key]
        if not isinstance(entry, dict) or set(entry) != LOCK_FILE_KEYS:
            mismatches.append(key.split("/", 1)[1])
            continue
        packaged_sha = entry.get("packaged_sha256")
        if not isinstance(packaged_sha, str) or _sha256(local_files[key]) != packaged_sha:
            mismatches.append(key.split("/", 1)[1])

    source_status = "LOCK_ONLY"
    source_reason = "upstream_root not provided; packaged files were checked against the closed lock"
    if upstream_root is not None:
        supplied_root = Path(upstream_root)
        if not supplied_root.is_dir():
            mismatches.append("upstream_root")
            source_status = "UNAVAILABLE"
            source_reason = f"upstream_root does not exist: {supplied_root}"
        else:
            source_files = _git_snapshot_files(supplied_root)
            source_status = "PINNED_GIT" if source_files is not None else "FILESYSTEM"
            source_reason = (
                "source bytes read from the pinned Git tree"
                if source_files is not None
                else "source bytes read from the supplied filesystem tree"
            )
            if source_files is None:
                source_files = _filesystem_snapshot_files(supplied_root)
            if set(source_files) != expected_keys:
                mismatches.append("upstream file inventory")
            for key in sorted(expected_keys & set(source_files)):
                entry = files[key]
                if not isinstance(entry, dict) or set(entry) != LOCK_FILE_KEYS:
                    continue
                git_blob, source_bytes = source_files[key]
                upstream_name, relative = key.split("/", 1)
                packaged_name = ALIASES[upstream_name]
                try:
                    expected_packaged = (
                        _skill_alias_bytes(source_bytes, upstream_name, packaged_name)
                        if relative == "SKILL.md"
                        else source_bytes
                    )
                except ValueError:
                    mismatches.append(relative)
                    continue
                if (
                    git_blob != entry.get("git_blob")
                    or _sha256(source_bytes) != entry.get("upstream_sha256")
                    or _sha256(expected_packaged) != entry.get("packaged_sha256")
                    or local_files.get(key) != expected_packaged
                ):
                    mismatches.append(relative)

    return ReelBenchSnapshotReport(
        revision=PINNED_REVISION,
        packaged_names=sorted(packaged_names),
        mismatches=sorted(set(mismatches)),
        source_status=source_status,
        source_reason=source_reason,
    )
