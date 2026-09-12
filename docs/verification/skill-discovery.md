# Skill discovery verification (Codex CLI)

> **Status:** `marketplace_install_and_skill_discovery = PASS`
> **Public marketplace:** PASS — `partme-ai-dreamina-design` installed from
> `https://github.com/partme-ai/codex-dreamina-design-plugin.git` @ `main`;
> `discovered: 14 / missing: none`.
> **Local personal marketplace:** PASS — same 14/14.
>
> Task 7 requires "Install from the public marketplace and verify Skill
> discovery in a fresh Codex task." This document records that
> verification for both the public and the local marketplace.

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

## 5. Public-marketplace install (the plan's literal requirement)

The repository's marketplace manifest points at
`https://github.com/partme-ai/codex-dreamina-design-plugin.git` ref `main`.
The merged work reached the remote, so the public path became testable and
was then executed:

```text
$ codex plugin marketplace add partme-ai/codex-dreamina-design-plugin@main
Added marketplace `partme-ai-dreamina-design` from
  https://github.com/partme-ai/codex-dreamina-design-plugin.git#main.
Installed marketplace root: ~/.codex/.tmp/marketplaces/partme-ai-dreamina-design

$ codex plugin list        # the new marketplace section
Marketplace `partme-ai-dreamina-design`
PLUGIN                                           STATUS         VERSION  PATH
codex-dreamina-design@partme-ai-dreamina-design  not installed           https://github.com/.../codex-dreamina-design-plugin.git, ref `main`
```

The plugin was offered straight from the repo's own
`.agents/plugins/marketplace.json` over the public URL — confirming the
manifest is consumed correctly by the real CLI from the public source.

The earlier personal-scoped install was then removed so the verification
would be unambiguous (only one marketplace could be satisfying the name),
and the plugin was re-installed from the public marketplace:

```text
$ codex plugin remove codex-dreamina-design@personal
Removed plugin `codex-dreamina-design` from marketplace `personal`.

$ codex plugin add codex-dreamina-design@partme-ai-dreamina-design
Added plugin `codex-dreamina-design` from marketplace `partme-ai-dreamina-design`.
Installed plugin root: ~/.codex/plugins/cache/partme-ai-dreamina-design/codex-dreamina-design/0.1.0
```

Resulting state and discovery:

```text
$ codex plugin list
Marketplace `partme-ai-dreamina-design`
codex-dreamina-design@partme-ai-dreamina-design  installed, enabled  0.1.0
    https://github.com/partme-ai/codex-dreamina-design-plugin.git, ref `main`

$ ls ~/.codex/plugins/cache/partme-ai-dreamina-design/codex-dreamina-design/0.1.0/skills | wc -l
14

$ codex debug prompt-input        # skill root r6 ->
r6 -> /Users/wandl/.codex/plugins/cache/partme-ai-dreamina-design/codex-dreamina-design/0.1.0/skills

discovered under codex-dreamina-design: 14
  codex-dreamina-design-use
  dreamina-cli
  dreamina-cli-image2image
  dreamina-cli-image2video
  dreamina-cli-text2image
  dreamina-cli-text2video
  dreamina-opencli-image2image
  dreamina-opencli-image2video
  dreamina-opencli-text2image
  dreamina-opencli-text2video
  dreamina-prompt-image2image
  dreamina-prompt-image2video
  dreamina-prompt-text2image
  dreamina-prompt-text2video
```

**Result: PASS** — the plan's literal requirement ("Install from the
public marketplace and verify Skill discovery in a fresh Codex task") is
satisfied. All 14 Skills are discovered from the marketplace-namespaced
cache path fed by the public GitHub repository.

Note: the remote `main` used for this install is `865f8d5`, which carries
all 14 Skills. The doc-only commit that follows this install
(`81e826a` and later) is not needed for install or discovery to work.

## 6. Reproducing the checks

```text
$ export PATH="$HOME/.nvm/versions/node/v24.18.0/bin:$PATH"   # node is not on PATH by default
$ codex plugin list | grep dreamina-design
$ codex debug prompt-input | python3 -c "...filter for codex-dreamina-design..."
```

`codex debug prompt-input` is read-only: it renders the prompt input list
without contacting a model, so it costs no quota.
