# Skill discovery verification (Codex CLI)

> **Status:** `marketplace_install_and_skill_discovery = PASS (local personal marketplace)`
> **Public-marketplace variant:** blocked — see §5.

Task 7 requires "Install from the public marketplace and verify Skill
discovery in a fresh Codex task." This document records that verification.

## 1. Install

The plugin was installed into the Codex **personal** marketplace using the
same pattern as the two pre-existing personal plugins
(`stitch-design@personal`, `codex-dreamina-3d@personal`):

* files copied to
  `~/.codex/plugins/cache/personal/codex-dreamina-design/0.1.0/`
  (14 Skill directories, no `.git`, no `__pycache__`);
* `[plugins."codex-dreamina-design@personal"] enabled = true` added to
  `~/.codex/config.toml`;
* an entry added to the personal marketplace registry
  `~/.agents/plugins/marketplace.json` with
  `source: {source: local, path: ./workspaces/workspace-partme-ai/codex-dreamina-design-plugin-wt}`.

Backups taken before editing:
`~/.codex/config.toml.bak-before-dreamina-design` and
`~/.agents/plugins/marketplace.json.bak-before-dreamina-design`.

## 2. Plugin registration confirmed

```text
$ codex plugin list
Marketplace `personal`
/Users/wandl/.agents/plugins/marketplace.json

PLUGIN                          STATUS              VERSION  PATH
stitch-design@personal          installed, enabled  0.3.0    .../codex-stitch-design-plugin
codex-dreamina-3d@personal      installed, enabled  0.1.0    .../codex-dreamina-3d-plugin
codex-dreamina-design@personal  installed, enabled  0.1.0    .../codex-dreamina-design-plugin-wt
```

## 3. Skill discovery — the bug this step caught

Discovery was verified with the read-only command
`codex debug prompt-input`, which renders the exact model-visible input
list (including the skills catalogue and the skill-root table).

**First run (before the fix): only 1 of 14 Skills was visible.**

```text
- codex-dreamina-design:codex-dreamina-design-use: Thin router Skill ... (r7/.../SKILL.md)
- codex-dreamina-design:dreamina-cli: 0 occurrences
- codex-dreamina-design:dreamina-cli-text2image: 0 occurrences
... 13 Skills silently missing
```

**Root cause.** The 13 packaged stubs used a *multi-line plain-scalar*
`description`:

```yaml
description: Stub for the upstream Dreamina Skill 'dreamina-cli'. Body is
fetched from ... and
must remain byte-identical to the pinned upstream commit.
```

An unindented continuation line is **invalid YAML**. Codex skips any Skill
whose frontmatter fails to parse, so all 13 Skills were invisible to the
model even though every required key was nominally present. The router
`codex-dreamina-design-use` used a `|` block scalar (valid YAML) and was
therefore discovered — which is exactly why the defect looked like a
partial success.

This is a **production-parser vs. test-parser** gap: the strict TRACE
validator only asserted that `name` and `description` *keys* were present,
never that the frontmatter block was parseable YAML. The gate was green
while the artifact was broken.

**Fix.**

1. All 13 `skills/dreamina-*/SKILL.md` descriptions rewritten as valid
   single-line YAML scalars (SHA pins preserved).
2. `scripts/scaffold_packaged_skills.py` template corrected so a
   regeneration cannot reintroduce the defect.
3. `scripts/run_strict_trace.py` gained
   `_validate_frontmatter_block()`, which rejects any unindented
   non-`key: value` line and reports:
   *"an unindented plain-scalar continuation makes the block unparseable
   YAML and the Skill will be silently skipped"*.
   Three RED-first tests cover it: the broken multi-line scalar is
   rejected, a valid single-line scalar passes, a `|` block scalar passes.

## 4. Skill discovery — after the fix

```text
$ codex debug prompt-input   # then filter for the plugin namespace

discovered: 14
missing:    none
```

All 14 Skills are now listed in the model-visible catalogue:

```text
- codex-dreamina-design:codex-dreamina-design-use: Thin router Skill for Dreamina Design
- codex-dreamina-design:dreamina-cli: Stub for the upstream Dreamina Skill 'dreamina-cli'
- codex-dreamina-design:dreamina-cli-image2image: ...
- codex-dreamina-design:dreamina-cli-image2video: ...
- codex-dreamina-design:dreamina-cli-text2image: ...
- codex-dreamina-design:dreamina-cli-text2video: ...
- codex-dreamina-design:dreamina-opencli-image2image: ...
- codex-dreamina-design:dreamina-opencli-image2video: ...
- codex-dreamina-design:dreamina-opencli-text2image: ...
- codex-dreamina-design:dreamina-opencli-text2video: ...
- codex-dreamina-design:dreamina-prompt-image2image: ...
- codex-dreamina-design:dreamina-prompt-image2video: ...
- codex-dreamina-design:dreamina-prompt-text2image: ...
- codex-dreamina-design:dreamina-prompt-text2video: ...
```

Regression guard: `python3 scripts/run_strict_trace.py --skills-root skills`
now reports `frontmatter_status = PASS` for all 14 Skills. (Its
`examples_status` remains FAIL for the 13 packaged stubs by design — the
plugin does not copy upstream Skill bodies; see `skill-trace.md`.)

## 5. Why the *public*-marketplace variant is blocked

The repository's marketplace manifest points at
`https://github.com/partme-ai/codex-dreamina-design-plugin.git` ref `main`.
At verification time:

```text
$ git ls-tree --name-only origin/main skills/
skills/.gitkeep
```

The public `main` still contains only `skills/.gitkeep` — none of the 14
Skills, because this work lives on the unpushed
`feat/dreamina-design-runtime` branch. Installing from the public
marketplace today would fetch an empty plugin. Merging the branch into
`main` and pushing is a repository decision the user owns; once that is
done, the identical `codex plugin list` / `codex debug prompt-input`
checks will pass against the public marketplace without any code change.

## 6. Reproducing the checks

```text
$ export PATH="$HOME/.nvm/versions/node/v24.18.0/bin:$PATH"   # node is not on PATH by default
$ codex plugin list | grep dreamina-design
$ codex debug prompt-input | python3 -c "...filter for codex-dreamina-design..."
```

`codex debug prompt-input` is read-only: it renders the prompt input list
without contacting a model, so it costs no quota.
