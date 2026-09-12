"""v7 distribution validator for the Dreamina Design plugin (Task 7).

Runs every check from ``scripts/validate_distribution.py`` and adds:

* Skill snapshot SHA consistency (each Skill's ``upstream_commit_sha``
  matches the verified upstream commit);
* link audit between ``plugin.json`` and ``marketplace.json``;
* symlink / cache directory detection;
* a stricter secret scan (PEM blocks, API key shapes);
* the canary gate: ``paid_canary`` is either ``APPROVED`` (when an
  approval marker file exists) or ``NOT_RUN`` — never silently
  consumed by CI;
* the read-only runtime contract: when the ``dreamina`` binary is not
  available on PATH, the verifier reports ``blocked`` with explicit
  instructions; it never silently passes.

The validator is intentionally read-only: it never installs the binary,
never authenticates, and never performs any generation.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


EXPECTED_SKILLS: tuple[str, ...] = (
    "dreamina-cli",
    "dreamina-cli-image2image",
    "dreamina-cli-image2video",
    "dreamina-cli-text2image",
    "dreamina-cli-text2video",
    "dreamina-opencli-image2image",
    "dreamina-opencli-image2video",
    "dreamina-opencli-text2image",
    "dreamina-opencli-text2video",
    "dreamina-prompt-image2image",
    "dreamina-prompt-image2video",
    "dreamina-prompt-text2image",
    "dreamina-prompt-text2video",
)

SECRET_PATTERNS: tuple[re.Pattern[bytes], ...] = (
    re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

# File globs whose contents are *not* treated as live secrets. The test
# suite legitimately embeds byte patterns that match the secret
# detectors (e.g. fixture strings for the secret-scan test itself).
SAFE_BASENAMES: frozenset[str] = frozenset(
    {
        "test_distribution_v7.py",
        "test_secret_scan.py",  # legacy name kept for safety
    }
)


class DistributionV7Error(Exception):
    """Base class for v7 verifier errors."""


class SecretLikeContentError(DistributionV7Error):
    """A secret-shaped byte pattern was detected in a non-test file."""


class SymlinkError(DistributionV7Error):
    """A symlink was found inside the plugin tree."""


class SkillSnapshotShaMismatchError(DistributionV7Error):
    """A packaged Skill's upstream_commit_sha does not match the verified commit."""


class ReadOnlyRuntimeBlockedError(DistributionV7Error):
    """The dreamina CLI is required but missing from PATH."""


class CanaryMissingError(DistributionV7Error):
    """The caller invoked ``require_paid_canary`` while no approval was recorded."""


@dataclass
class SecretMatch:
    path: str
    pattern: str


@dataclass
class DistributionV7Report:
    skill_count: int = 0
    skill_snapshot_status: str = "NOT_RUN"
    skill_snapshot_mismatches: list[str] = field(default_factory=list)
    secret_matches: list[SecretMatch] = field(default_factory=list)
    symlinks: list[str] = field(default_factory=list)
    marketplace_url_matches: bool = False
    repository_url: str = ""
    paid_canary: str = "NOT_RUN"
    read_only_runtime_contract: str = "blocked"
    read_only_runtime_reason: str = ""
    legacy_validator_exit_code: int = 0

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2, sort_keys=True)


class DistributionV7Verifier:
    """Run every v7 distribution check against the plugin root."""

    def __init__(
        self,
        *,
        root: Path,
        expected_upstream_sha: str | None = None,
        dreamina_command: str = "dreamina",
    ) -> None:
        self._root = Path(root)
        self._expected_upstream_sha = expected_upstream_sha
        self._dreamina_command = dreamina_command

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    def run(self) -> DistributionV7Report:
        report = DistributionV7Report()
        self._run_legacy_validator(report)
        self._run_skill_snapshot_check(report)
        self._run_secret_scan(report)
        self._run_symlink_scan(report)
        self._run_link_audit(report)
        self._run_canary_check(report)
        self._run_runtime_contract_check(report)
        return report

    def require_read_only_runtime(self) -> None:
        if not self._dreamina_available():
            raise ReadOnlyRuntimeBlockedError(
                "dreamina CLI is not available; "
                "read-only runtime contract is BLOCKED — install and authorize "
                "the CLI before requesting PASS"
            )

    def require_paid_canary(self) -> None:
        if not self._canary_approved():
            raise CanaryMissingError(
                "paid canary is NOT_RUN; either record an explicit approval "
                "via docs/verification/paid-canary-approved.md or treat "
                "the canary as a separate action-time approval"
            )

    # ------------------------------------------------------------------
    # Check implementations
    # ------------------------------------------------------------------
    def _run_legacy_validator(self, report: DistributionV7Report) -> None:
        legacy_script = Path(__file__).resolve().parent / "validate_distribution.py"
        if not legacy_script.is_file():
            report.legacy_validator_exit_code = -1
            return
        result = subprocess.run(  # noqa: S603 — argv-only
            [sys.executable, str(legacy_script), str(self._root)],
            capture_output=True,
            text=True,
            check=False,
        )
        report.legacy_validator_exit_code = result.returncode

    def _run_skill_snapshot_check(self, report: DistributionV7Report) -> None:
        skills_root = self._root / "skills"
        if not skills_root.is_dir():
            report.skill_snapshot_status = "FAIL"
            report.skill_snapshot_mismatches.append("skills/ directory missing")
            return
        found = 0
        for name in EXPECTED_SKILLS:
            skill_md = skills_root / name / "SKILL.md"
            if not skill_md.is_file():
                report.skill_snapshot_mismatches.append(f"{name}: SKILL.md missing")
                continue
            found += 1
            front = self._parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            sha = front.get("upstream_commit_sha", "").strip()
            if not sha:
                report.skill_snapshot_mismatches.append(f"{name}: upstream_commit_sha missing")
                continue
            if self._expected_upstream_sha is None:
                continue
            if sha != self._expected_upstream_sha:
                report.skill_snapshot_mismatches.append(
                    f"{name}: upstream_commit_sha {sha} != expected {self._expected_upstream_sha}"
                )
        report.skill_count = found
        # Promote to PASS whenever every Skill is present and pins a real SHA;
        # downstream callers can compare the SHA against the cloned upstream
        # HEAD via ``scripts/verify_skill_snapshot.py``.
        if not found:
            report.skill_snapshot_status = "FAIL"
        elif report.skill_snapshot_mismatches:
            report.skill_snapshot_status = "FAIL"
        elif self._expected_upstream_sha is not None:
            report.skill_snapshot_status = "PASS"
        else:
            report.skill_snapshot_status = "NOT_RUN"

    def _run_secret_scan(self, report: DistributionV7Report) -> None:
        for target in self._root.rglob("*"):
            if not target.is_file() or ".git" in target.parts:
                continue
            if target.name in SAFE_BASENAMES:
                continue
            if target.suffix in {".pyc", ".pyo", ".pyd"}:
                continue
            try:
                data = target.read_bytes()
            except OSError:
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(data):
                    report.secret_matches.append(
                        SecretMatch(path=str(target.relative_to(self._root)), pattern=pattern.pattern.decode("ascii", errors="replace"))
                    )

    def _run_symlink_scan(self, report: DistributionV7Report) -> None:
        for entry in self._root.rglob("*"):
            if entry.is_symlink() and ".git" not in entry.parts:
                report.symlinks.append(str(entry.relative_to(self._root)))

    def _run_link_audit(self, report: DistributionV7Report) -> None:
        manifest_path = self._root / ".codex-plugin" / "plugin.json"
        marketplace_path = self._root / ".agents" / "plugins" / "marketplace.json"
        if not (manifest_path.is_file() and marketplace_path.is_file()):
            return
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        repository = manifest.get("repository", "")
        report.repository_url = repository
        expected_source = {"source": "url", "url": repository + ".git", "ref": "main"}
        for entry in marketplace.get("plugins", []):
            if entry.get("name") == manifest.get("name"):
                report.marketplace_url_matches = entry.get("source") == expected_source
                return
        report.marketplace_url_matches = False

    def _run_canary_check(self, report: DistributionV7Report) -> None:
        marker = self._root / "docs" / "verification" / "paid-canary-approved.md"
        if marker.is_file():
            report.paid_canary = "APPROVED"
        else:
            report.paid_canary = "NOT_RUN"

    def _canary_approved(self) -> bool:
        marker = self._root / "docs" / "verification" / "paid-canary-approved.md"
        return marker.is_file()

    def _run_runtime_contract_check(self, report: DistributionV7Report) -> None:
        if self._dreamina_available():
            report.read_only_runtime_contract = "observed"
            report.read_only_runtime_reason = (
                f"{self._dreamina_command} found on PATH; ready for read-only "
                "version/help/schema probes after separate authorization"
            )
        else:
            report.read_only_runtime_contract = "blocked"
            report.read_only_runtime_reason = (
                f"{self._dreamina_command} not found on PATH; "
                "install the dreamina CLI and authorize its use before requesting PASS"
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _dreamina_available(self) -> bool:
        binary = shutil.which(self._dreamina_command)
        if binary:
            return True
        # Allow explicit absolute paths too.
        if os.path.sep in self._dreamina_command and Path(self._dreamina_command).exists():
            return os.access(self._dreamina_command, os.X_OK)
        return False

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, str]:
        if not text.startswith("---"):
            return {}
        end = text.find("\n---", 3)
        if end == -1:
            return {}
        block = text[3:end].strip()
        parsed: dict[str, str] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            parsed[key.strip()] = value.strip()
        return parsed


def main(argv: list[str] | None = None) -> int:
    parser_args = argv if argv is not None else sys.argv[1:]
    root = Path(".").resolve()
    expected_sha: str | None = None
    strict = False
    i = 0
    while i < len(parser_args):
        arg = parser_args[i]
        if arg == "--root" and i + 1 < len(parser_args):
            root = Path(parser_args[i + 1]).resolve()
            i += 2
            continue
        if arg == "--expected-upstream-sha" and i + 1 < len(parser_args):
            expected_sha = parser_args[i + 1]
            i += 2
            continue
        if arg == "--strict":
            strict = True
            i += 1
            continue
        i += 1
    verifier = DistributionV7Verifier(root=root, expected_upstream_sha=expected_sha)
    report = verifier.run()
    print(report.to_json())
    if report.legacy_validator_exit_code != 0:
        return 1
    if report.secret_matches:
        return 2
    if report.symlinks:
        return 3
    if expected_sha is not None and report.skill_snapshot_status != "PASS":
        return 4
    if strict and report.read_only_runtime_contract != "observed":
        return 5
    if strict and report.paid_canary != "APPROVED":
        return 6
    return 0


__all__ = [
    "CanaryMissingError",
    "DistributionV7Error",
    "DistributionV7Report",
    "DistributionV7Verifier",
    "EXPECTED_SKILLS",
    "ReadOnlyRuntimeBlockedError",
    "SecretLikeContentError",
    "SkillSnapshotShaMismatchError",
    "SymlinkError",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
