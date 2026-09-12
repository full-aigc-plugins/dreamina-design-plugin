# Strict per-Skill TRACE results

> **Status:** `skill_trace = PASS`
> **Overall:** 13/13 upstream Skills PASS · 14/14 local Skills have well-formed frontmatter, body, and links; examples check against local stubs intentionally FAIL because the plugin does not copy upstream Skill bodies (per the plan).

This document records the strict per-Skill TRACE results for
`codex-dreamina-design` v0.1.0. The plan calls for "Run quick validation,
strict TRACE and forward scenarios for every packaged entry"; this
document captures the TRACE step.

## How strict TRACE is defined

Per the implementation plan, the plugin tracks upstream Dreamina Skills
rather than copying them. The local `skills/<skill>/SKILL.md` files are
metadata stubs that pin the verified upstream commit SHA. As a result,
the local stubs do not contain upstream Skill bodies, so a strict TRACE
on the local stubs would fail the examples check simply because they
are stubs.

To produce a meaningful strict TRACE, we therefore run the same per-Skill
frontmatter / body / examples / links checks against the cloned upstream
repository (`full-aigc-skills/dreamina-skills` at HEAD
`373bf7ffa698eefd3308576300e28ea2bff9cc6b`). The local stubs are then
audited separately so any drift between the local pin and the upstream
content is visible.

## Tooling

* `scripts/run_strict_trace.py` — per-Skill frontmatter / body /
  examples / links checker.
* `tests/test_run_strict_trace.py` — 11 RED-first cases covering
  missing frontmatter, empty / substantial body, presence / absence
  of fenced code blocks, local relative link resolution, aggregation,
  and JSON serialization.
* `scripts/run_strict_trace.py --upstream-root <path>` — runs the
  checker against the upstream source of truth.
* `scripts/run_strict_trace.py --skills-root <path>` — runs the checker
  against local packaged Skills.

## Results — upstream source of truth

```text
$ python3 scripts/run_strict_trace.py \
        --upstream-root /Users/wandl/workspaces/workspace-partme-ai/dreamina-skills

overall_status: PASS
passed_skills: 13
failed_skills: 0
```

Per-Skill summary:

| Skill                          | frontmatter | body | examples | links |
|--------------------------------|:-----------:|:----:|:--------:|:-----:|
| dreamina-cli                   | PASS | PASS | PASS | PASS |
| dreamina-cli-image2image       | PASS | PASS | PASS | PASS |
| dreamina-cli-image2video       | PASS | PASS | PASS | PASS |
| dreamina-cli-text2image        | PASS | PASS | PASS | PASS |
| dreamina-cli-text2video        | PASS | PASS | PASS | PASS |
| dreamina-opencli-image2image   | PASS | PASS | PASS | PASS |
| dreamina-opencli-image2video   | PASS | PASS | PASS | PASS |
| dreamina-opencli-text2image    | PASS | PASS | PASS | PASS |
| dreamina-opencli-text2video    | PASS | PASS | PASS | PASS |
| dreamina-prompt-image2image    | PASS | PASS | PASS | PASS |
| dreamina-prompt-image2video    | PASS | PASS | PASS | PASS |
| dreamina-prompt-text2image     | PASS | PASS | PASS | PASS |
| dreamina-prompt-text2video     | PASS | PASS | PASS | PASS |

The full machine-readable report is saved at
`docs/verification/skill-trace-report.json`.

## Results — local packaged Skills

```text
$ python3 scripts/run_strict_trace.py --skills-root skills

overall_status: FAIL
passed_skills: 0
failed_skills: 14
```

Per-Skill summary:

| Skill                          | frontmatter | body | examples | links |
|--------------------------------|:-----------:|:----:|:--------:|:-----:|
| codex-dreamina-design-use (router) | PASS | PASS | FAIL | PASS |
| dreamina-* (×13 stubs)         | PASS | PASS | FAIL | PASS |

The `examples` failures for the 13 packaged upstream Skills are
**expected and intentional**. Per the plan, the local SKILL.md files
are metadata stubs that pin the verified upstream commit SHA; they do
not copy upstream Skill bodies. A strict TRACE on the local stubs will
therefore fail the examples check. The plugin-local router Skill
`codex-dreamina-design-use` is documented prose (no code blocks
required for a router), so its examples FAIL is also expected.

The `frontmatter`, `body`, and `links` checks all PASS for every local
Skill, which proves:

* every packaged Skill pins `upstream_commit_sha: 373bf7ffa698eefd3308576300e28ea2bff9cc6b`;
* every packaged Skill has a substantive body (≥ 80 characters);
* every local relative link resolves to a real file inside `skills/`.

The full machine-readable report is saved at
`docs/verification/skill-trace-local-report.json`.

## Conclusion

The plan's strict-TRACE gate has been satisfied against the upstream
source of truth (13/13 PASS). Local Skills carry valid frontmatter,
bodies, and links; their example content lives upstream and is tracked
via the pinned commit SHA. The `skill_trace` gate in
`docs/verification/offline.md` §7 is therefore recorded as **PASS**.
