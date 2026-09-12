"""Replace the ``upstream_commit_sha`` placeholder in every packaged Skill.

The scaffold script (``scripts/scaffold_packaged_skills.py``) writes a
stub ``SKILL.md`` with ``upstream_commit_sha: NOT_VERIFIED``. After the
upstream ``full-aigc-skills/dreamina-skills`` repository has been
cloned and its commit SHA verified, this script patches each packaged
Skill in place — it does **not** rewrite any other field and does not
copy the upstream Skill body. Body byte-parity is verified separately
by ``scripts/verify_skill_snapshot.py``.

Usage:
    python3 scripts/pin_upstream_sha.py <upstream-commit-sha>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKILL_NAMES = (
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


_SHA_PATTERN = re.compile(r"^upstream_commit_sha:\s*.*$", re.MULTILINE)


def pin(skills_root: Path, sha: str) -> list[Path]:
    """Patch ``upstream_commit_sha`` in each packaged Skill's SKILL.md."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError(f"expected 40-char lowercase hex SHA, got {sha!r}")
    patched: list[Path] = []
    for name in SKILL_NAMES:
        skill_md = skills_root / name / "SKILL.md"
        if not skill_md.is_file():
            print(f"WARN: missing {skill_md}", file=sys.stderr)
            continue
        text = skill_md.read_text(encoding="utf-8")
        if _SHA_PATTERN.search(text) is None:
            print(f"WARN: no upstream_commit_sha field in {skill_md}", file=sys.stderr)
            continue
        new_text = _SHA_PATTERN.sub(f"upstream_commit_sha: {sha}", text, count=1)
        skill_md.write_text(new_text, encoding="utf-8")
        patched.append(skill_md)
    return patched


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: pin_upstream_sha.py <upstream-commit-sha> [skills-root]", file=sys.stderr)
        return 2
    sha = args[0]
    skills_root = Path(args[1]) if len(args) > 1 else Path("skills").resolve()
    patched = pin(skills_root, sha)
    print(f"pinned upstream_commit_sha={sha} in {len(patched)} Skills")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
