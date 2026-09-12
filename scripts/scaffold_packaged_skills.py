"""Scaffold the 13 packaged Dreamina Skill directories.

Each Skill directory contains only a ``SKILL.md`` stub that:

* pins the upstream commit SHA reference (``upstream_commit_sha``);
* states explicitly that the Skill body must remain byte-identical to
  ``full-aigc-skills/dreamina-skills`` at the pinned commit;
* does NOT copy the upstream Skill body. The body is fetched on demand
  by ``scripts/verify_skill_snapshot.py`` once the upstream repository
  is available.

Until then, ``scripts/verify_skill_snapshot.py`` reports the byte-parity
check as ``NOT_RUN`` with an explicit reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Placeholder upstream SHA — to be replaced with a real verified commit
# once full-aigc-skills/dreamina-skills is cloned locally.
PLACEHOLDER_UPSTREAM_SHA = "NOT_VERIFIED"


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


def scaffold(skills_root: Path) -> list[Path]:
    skills_root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for name in SKILL_NAMES:
        skill_dir = skills_root / name
        skill_dir.mkdir(exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\n"
            f"name: {name}\n"
            f"description: Stub for the upstream Dreamina Skill '{name}'. "
            "Body is fetched from full-aigc-skills/dreamina-skills on demand and\n"
            "must remain byte-identical to the pinned upstream commit.\n"
            f"upstream_commit_sha: {PLACEHOLDER_UPSTREAM_SHA}\n"
            "upstream_repository: https://github.com/full-aigc-skills/dreamina-skills\n"
            "do_not_edit_body: true\n"
            "---\n\n"
            "# Stub body\n\n"
            f"This Skill is a thin metadata stub for `{name}`. Its body is **not**\n"
            "copied into the plugin repository. The Skill body lives upstream at\n"
            "`full-aigc-skills/dreamina-skills` and must remain byte-identical\n"
            "to the pinned upstream commit. Use\n"
            "`scripts/verify_skill_snapshot.py --upstream-root <path>` to verify\n"
            "parity. Until the upstream repository is available locally, the\n"
            "parity check is reported as `NOT_RUN`.\n",
            encoding="utf-8",
        )
        created.append(skill_dir)
    return created


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    skills_root = Path(args[0]) if args else Path("skills").resolve()
    created = scaffold(skills_root)
    print(f"scaffolded {len(created)} Skill directories under {skills_root}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
