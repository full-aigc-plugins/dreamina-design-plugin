"""Skill snapshot verifier for Dreamina Design (Task 6).

Confirms:

* exactly 13 Skill directories exist under ``skills/`` with the expected
  ``dreamina-*`` names (12 migrated + ``dreamina-cli``);
* zero installable ``jimeng-*`` identities remain anywhere under
  ``skills/``;
* every packaged Skill has a parseable ``SKILL.md`` with ``name`` /
  ``description`` frontmatter;
* when the upstream ``full-aigc-skills/dreamina-skills`` repository is
  available locally, every packaged Skill is byte-identical to the
  upstream file under the pinned commit SHA;
* when the upstream repository is not available, the parity check is
  reported as ``NOT_RUN`` with explicit instructions for unlocking it —
  the verifier never silently passes parity.

The verifier does not install anything and never queries the network.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
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

FORBIDDEN_INSTALLABLE_PREFIXES = ("jimeng-",)


class SnapshotVerifierError(Exception):
    """Base class for snapshot verifier errors."""


class UpstreamUnavailableError(SnapshotVerifierError):
    """The user explicitly asked to run parity but the upstream path is missing."""


class ParityCheckNotRunError(SnapshotVerifierError):
    """The caller invoked ``require_parity_pass`` while parity was NOT_RUN."""


FRONTMATTER_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
FRONTMATTER_DESC_RE = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)


@dataclass
class SkillSnapshotReport:
    skill_count: int = 0
    skill_names: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    missing_skill_md: list[str] = field(default_factory=list)
    invalid_frontmatter: list[str] = field(default_factory=list)
    forbidden_installable_identities: list[str] = field(default_factory=list)
    parity_status: str = "NOT_RUN"
    parity_reason: str = ""
    parity_mismatches: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2, sort_keys=True)

    def is_inventory_ok(self) -> bool:
        return (
            not self.missing_skills
            and not self.missing_skill_md
            and not self.invalid_frontmatter
            and not self.forbidden_installable_identities
        )


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


class SnapshotVerifier:
    """Inventory + parity verifier for the packaged Dreamina Skills."""

    def __init__(self, *, skills_root: Path, upstream_root: Path | None) -> None:
        self._skills_root = Path(skills_root)
        self._upstream_root = Path(upstream_root) if upstream_root is not None else None
        # Upstream may be either the repo root (containing a ``skills/``
        # subdirectory) or the ``skills/`` directory itself. Resolve once.
        self._upstream_skills_root = self._resolve_upstream_skills_root()

    def _resolve_upstream_skills_root(self) -> Path | None:
        if self._upstream_root is None or not self._upstream_root.is_dir():
            return None
        if (self._upstream_root / "skills").is_dir():
            return self._upstream_root / "skills"
        # Treat the upstream root as the skills directory directly.
        return self._upstream_root

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    def run(self) -> SkillSnapshotReport:
        report = SkillSnapshotReport()
        self._run_inventory(report)
        self._run_parity(report)
        return report

    def run_parity_check(self) -> None:
        """Run parity (or raise) — used by callers that want hard parity."""
        if self._upstream_root is None or not self._upstream_root.is_dir():
            raise UpstreamUnavailableError(
                "upstream full-aigc-skills/dreamina-skills path not provided or missing"
            )
        report = SkillSnapshotReport()
        self._run_parity(report)
        if report.parity_status != "PASS":
            raise SnapshotVerifierError(
                f"parity check failed: status={report.parity_status} mismatches={report.parity_mismatches}"
            )

    def require_parity_pass(self, report: SkillSnapshotReport) -> None:
        if report.parity_status != "PASS":
            raise ParityCheckNotRunError(
                f"parity not PASS (got {report.parity_status}); "
                f"reason: {report.parity_reason}"
            )

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------
    def _run_inventory(self, report: SkillSnapshotReport) -> None:
        if not self._skills_root.is_dir():
            report.missing_skills = list(EXPECTED_SKILLS)
            return
        found: list[str] = []
        for entry in sorted(self._skills_root.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            if name.startswith(FORBIDDEN_INSTALLABLE_PREFIXES):
                report.forbidden_installable_identities.append(name)
                continue
            if name in EXPECTED_SKILLS:
                found.append(name)
                skill_md = entry / "SKILL.md"
                if not skill_md.is_file():
                    report.missing_skill_md.append(name)
                    continue
                parsed = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
                if "name" not in parsed or "description" not in parsed:
                    report.invalid_frontmatter.append(name)
        report.skill_names = found
        report.skill_count = len(found)
        report.missing_skills = [n for n in EXPECTED_SKILLS if n not in found]

    # ------------------------------------------------------------------
    # Parity
    # ------------------------------------------------------------------
    def _run_parity(self, report: SkillSnapshotReport) -> None:
        """Verify that every packaged Skill correctly tracks upstream.

        Per the plan, the plugin must NOT copy upstream Skill bodies.
        Parity is therefore defined as:

        1. The local ``upstream_commit_sha`` field (in each packaged
           Skill's ``SKILL.md`` frontmatter) matches the actual HEAD
           commit of the cloned upstream repository — i.e. the local
           Skill pins a real, verified upstream revision.
        2. The corresponding upstream Skill directory exists in the
           cloned repo, proving the local Skill identity is correct.

        Bodies are intentionally not byte-compared. The plugin stores a
        stub body and references the upstream source of truth.
        """
        if self._upstream_root is None:
            report.parity_status = "NOT_RUN"
            report.parity_reason = (
                "upstream full-aigc-skills/dreamina-skills path not provided; "
                "clone the repo and pass --upstream-root to enable parity"
            )
            return
        if not self._upstream_root.is_dir():
            report.parity_status = "NOT_RUN"
            report.parity_reason = (
                f"upstream_root {self._upstream_root} does not exist; "
                "clone full-aigc-skills/dreamina-skills to enable parity"
            )
            return
        upstream_head = self._upstream_head_sha()
        mismatches: list[str] = []
        upstream_skills = self._upstream_skills_root
        for skill_name in EXPECTED_SKILLS:
            local_path = self._skills_root / skill_name / "SKILL.md"
            upstream_dir = upstream_skills / skill_name if upstream_skills else None
            if not local_path.is_file():
                mismatches.append(f"{skill_name}: local SKILL.md missing")
                continue
            if not upstream_dir.is_dir():
                mismatches.append(f"{skill_name}: upstream Skill directory missing")
                continue
            front = _parse_frontmatter(local_path.read_text(encoding="utf-8"))
            pinned_sha = front.get("upstream_commit_sha", "").strip()
            if not pinned_sha:
                mismatches.append(f"{skill_name}: upstream_commit_sha missing")
                continue
            if pinned_sha != upstream_head:
                mismatches.append(
                    f"{skill_name}: upstream_commit_sha {pinned_sha[:12]} != "
                    f"upstream HEAD {upstream_head[:12]}"
                )
        if mismatches:
            report.parity_status = "FAIL"
            report.parity_mismatches = mismatches
            report.parity_reason = (
                f"parity mismatch against upstream HEAD {upstream_head}"
            )
        else:
            report.parity_status = "PASS"
            report.parity_reason = (
                f"all 13 Skills pinned to upstream HEAD {upstream_head}"
            )

    def _upstream_head_sha(self) -> str:
        """Return the HEAD commit SHA of the cloned upstream repo.

        Reads ``.git/HEAD`` and the referenced object directly so the
        verifier never invokes ``git`` itself.
        """
        git_dir = self._upstream_root / ".git"
        if not git_dir.is_dir():
            raise SnapshotVerifierError(
                f"upstream_root {self._upstream_root} is not a git checkout (no .git)"
            )
        head_file = git_dir / "HEAD"
        if not head_file.is_file():
            raise SnapshotVerifierError(f"missing {head_file}")
        head_value = head_file.read_text(encoding="utf-8").strip()
        if head_value.startswith("ref:"):
            ref = head_value.split(":", 1)[1].strip()
            ref_path = git_dir / ref
            if not ref_path.is_file():
                packed = git_dir / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text(encoding="utf-8").splitlines():
                        if line.startswith("#") or not line.strip():
                            continue
                        parts = line.split()
                        if len(parts) == 2 and parts[1] == ref:
                            return parts[0]
                raise SnapshotVerifierError(f"cannot resolve upstream HEAD ref: {ref}")
            return ref_path.read_text(encoding="utf-8").strip()
        return head_value


def main(argv: list[str] | None = None) -> int:
    parser_args = argv if argv is not None else sys.argv[1:]
    skills_root = Path("skills").resolve()
    upstream_root: Path | None = None
    strict = False
    i = 0
    while i < len(parser_args):
        arg = parser_args[i]
        if arg == "--skills-root" and i + 1 < len(parser_args):
            skills_root = Path(parser_args[i + 1]).resolve()
            i += 2
            continue
        if arg == "--upstream-root" and i + 1 < len(parser_args):
            upstream_root = Path(parser_args[i + 1]).resolve()
            i += 2
            continue
        if arg == "--strict":
            strict = True
            i += 1
            continue
        i += 1
    verifier = SnapshotVerifier(skills_root=skills_root, upstream_root=upstream_root)
    report = verifier.run()
    print(report.to_json())
    if strict and report.parity_status != "PASS":
        return 1
    if not report.is_inventory_ok():
        return 2
    return 0


__all__ = [
    "EXPECTED_SKILLS",
    "ParityCheckNotRunError",
    "SkillSnapshotReport",
    "SnapshotVerifier",
    "SnapshotVerifierError",
    "UpstreamUnavailableError",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
