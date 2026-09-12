# Strict Skill TRACE results

> Current audit: 2026-09-13

The plugin now packages complete Skill trees rather than metadata stubs.

| Scope | Result |
|---|---:|
| Local plugin Skills | 14/14 PASS |
| Pinned upstream Design Skills | 13/13 PASS |
| Byte parity | 13/13 PASS |
| Pinned commit | `300bfc1d649a68c1802a43aa7a64c50000e095d4` |

Commands:

```bash
python3 scripts/run_strict_trace.py --skills-root skills
python3 scripts/run_strict_trace.py \
  --upstream-root /Users/wandl/workspaces/workspace-partme-ai/dreamina-skills
python3 scripts/verify_skill_snapshot.py \
  --upstream-root /Users/wandl/workspaces/workspace-partme-ai/dreamina-skills \
  --strict
```

The package-wide source pin is `skills/.upstream-commit`; upstream SKILL.md
frontmatter is not modified during packaging.
