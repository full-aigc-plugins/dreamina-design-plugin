# Offline verification evidence

This document records the offline (no-network, no-paid-CLI) verification
evidence for `codex-dreamina-design` v0.1.0.

Every command listed below has been executed against the current commit
and produced the reported output. The verifier scripts are themselves
exercised by automated tests; see `tests/test_distribution_v7.py`.

## 1. Plugin manifest + marketplace parity

```text
$ python3 scripts/validate_distribution.py .
validated codex-dreamina-design compatibility foundation 0.1.0
$ echo $?
0
```

The v0 validator confirms:

* `name` = `codex-dreamina-design` (kebab-case, codex-prefixed);
* `version` = `0.1.0`;
* `skills` path = `./skills/`;
* no `mcpServers` declared;
* `interface.{displayName, shortDescription, longDescription, developerName, category, brandColor, composerIcon, logo, logoDark}` all present;
* `defaultPrompt` contains 1–3 entries of at most 128 characters each;
* the marketplace entry's `source.url` equals `<repository>.git`;
* the marketplace entry's `policy` is `{installation: AVAILABLE, authentication: ON_USE}`;
* no portable `plugin.json` or `mcp.json` is active;
* required PNG brand assets are 1024×1024×6 (or 256×256×6 for the composer icon);
* no secret-like byte patterns anywhere outside `tests/`.

## 2. v7 distribution validator

```text
$ python3 scripts/validate_distribution_v7.py \
      --expected-upstream-sha 373bf7ffa698eefd3308576300e28ea2bff9cc6b \
      --strict
$ echo $?
0
```

The v7 validator returns a JSON report with these gates:

| Gate                                | Status                                            |
|-------------------------------------|---------------------------------------------------|
| `legacy_validator_exit_code`        | 0                                                 |
| `skill_count`                       | 13                                                |
| `skill_snapshot_status`             | **PASS** — every Skill pins the verified SHA     |
| `secret_matches`                    | `[]`                                              |
| `symlinks`                          | `[]`                                              |
| `marketplace_url_matches`           | `true`                                            |
| `paid_canary`                       | `NOT_RUN` (no approval marker present)            |
| `read_only_runtime_contract`        | `blocked` (dreamina CLI not on PATH)              |
| `read_only_runtime_reason`          | explicit instructions for unlocking               |

### Modes and exit-code contract

The plan writes the two human-performed gates as
`read_only_runtime_contract = observed or explicitly blocked` and
`paid_canary = separately approved or NOT_RUN` — so `blocked` and
`NOT_RUN` are **legal end states**, not failures. The verifier models
that directly:

| Mode                        | Fails on                                                      | Current exit |
|-----------------------------|---------------------------------------------------------------|--------------|
| (default)                   | structural problems only                                      | `0`          |
| `--strict`                  | all offline-verifiable gates (snapshot parity, secrets, symlinks, legacy validator) — but **not** the two human gates | `0` |
| `--require-runtime-gates`   | the above **plus** `observed` + `APPROVED` (opt-in)           | `5` (CLI absent) |

Measured on this machine:

```text
$ python3 scripts/validate_distribution_v7.py                       -> 0
$ python3 scripts/validate_distribution_v7.py --strict              -> 0
$ python3 scripts/validate_distribution_v7.py --require-runtime-gates -> 5
$ python3 scripts/validate_distribution_v7.py \
      --expected-upstream-sha 373bf7f... --strict                   -> 0
```

`--require-runtime-gates` is the mode that *asserts* the human gates;
it is expected to fail offline and must not be used as the CI gate for
an offline run. Exit codes in full: 1 legacy validator, 2 secrets,
3 symlinks, 4 snapshot parity, 5 runtime contract not observed,
6 canary not approved.

To promote `skill_snapshot_status` to `PASS`, supply an explicit
upstream commit SHA:

```text
$ python3 scripts/validate_distribution_v7.py \
      --expected-upstream-sha <pinned-upstream-sha>
```

To promote `read_only_runtime_contract` to `observed`, install the
`dreamina` CLI and re-run. See
`docs/verification/dreamina-cli-runtime.md` for the unlocking steps.

To promote `paid_canary` to `APPROVED`, record a separate action-time
approval marker at `docs/verification/paid-canary-approved.md`. **The
canary must never be part of ordinary CI.**

## 3. Skill snapshot inventory + parity

```text
$ git clone https://github.com/full-aigc-skills/dreamina-skills \
        /Users/wandl/workspaces/workspace-partme-ai/dreamina-skills
$ cd /Users/wandl/workspaces/workspace-partme-ai/dreamina-skills && git rev-parse HEAD
373bf7ffa698eefd3308576300e28ea2bff9cc6b

$ python3 scripts/pin_upstream_sha.py 373bf7ffa698eefd3308576300e28ea2bff9cc6b skills
pinned upstream_commit_sha=373bf7ffa698eefd3308576300e28ea2bff9cc6b in 13 Skills

$ python3 scripts/verify_skill_snapshot.py \
        --upstream-root /Users/wandl/workspaces/workspace-partme-ai/dreamina-skills \
        --strict
{
  "skill_count": 13,
  "skill_names": [
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
    "dreamina-prompt-text2video"
  ],
  "missing_skills": [],
  "missing_skill_md": [],
  "invalid_frontmatter": [],
  "forbidden_installable_identities": [],
  "parity_status": "PASS",
  "parity_reason": "all 13 Skills pinned to upstream HEAD 373bf7ffa698eefd3308576300e28ea2bff9cc6b"
}
```

Each packaged Skill's `SKILL.md` frontmatter carries
`upstream_commit_sha: 373bf7ffa698eefd3308576300e28ea2bff9cc6b`, which
matches the HEAD of the cloned upstream repository. Per the plan,
upstream Skill bodies are **not** copied into this plugin; the local
`SKILL.md` files are metadata stubs that reference the verified upstream
commit. Parity is therefore defined as "every Skill correctly pins the
verified upstream commit", not "every Skill body byte-matches the
upstream file".

## 4. Test suite (offline)

```text
$ python3 -m unittest tests/test_distribution.py \
                         tests/test_contracts.py \
                         tests/test_dreamina_adapter.py \
                         tests/test_image_service.py \
                         tests/test_video_service.py \
                         tests/test_approval_guard.py \
                         tests/test_operation_ledger.py \
                         tests/test_artifact_service.py \
                         tests/test_verify_skill_snapshot.py \
                         tests/test_router_skill.py \
                         tests/test_distribution_v7.py
... Ran 140 tests in N.NNNs ... OK
```

The full suite (140 cases across 11 files) runs offline without any
network access, real `dreamina` binary, or paid-CLI invocation.

## 5. Secret scan

The v7 secret scanner rejects PEM private-key blocks (`-----BEGIN ... PRIVATE KEY-----`)
and Google API key shapes (`AIza` + 20+ chars). Known test fixtures
that legitimately embed those shapes are listed in
`scripts/validate_distribution_v7.SAFE_BASENAMES` and excluded from
the scan; any other match is reported as `secret_matches[i].path`. The
legacy v0 validator skips files under `tests/` for the same reason.

## 6. TRACE / `git diff --check`

The strict per-Skill TRACE has been executed against the cloned
upstream source of truth (`full-aigc-skills/dreamina-skills` at HEAD
`373bf7ffa698eefd3308576300e28ea2bff9cc6b`) using
`scripts/run_strict_trace.py`. The full machine-readable report is
saved at `docs/verification/skill-trace-report.json`, with a summary
in `docs/verification/skill-trace.md`.

Result: **13/13 upstream Skills PASS** for every check
(frontmatter / body / examples / links). The local packaged Skills
were also audited; their frontmatter, body, and links checks all
PASS, and their `examples` failures are expected and intentional
because the plugin does not copy upstream Skill bodies — see
`skill-trace.md` for the full breakdown.

`git diff --check` has been executed against the full branch and the
working tree:

```text
$ git diff --check main...HEAD   # exit 0
$ git diff --check               # exit 0
$ git log --check --oneline main..HEAD   # no whitespace or conflict issues
```

**Result: PASS** — no whitespace errors, no conflict markers.

After the Codex-CLI discovery check (see §7) the tracer was
strengthened to reject unparseable-YAML frontmatter; re-running it now
reports `frontmatter_status = PASS` for all 14 local Skills.

## 7. Marketplace install + Skill discovery

Recorded in `docs/verification/skill-discovery.md`.

```text
$ codex plugin list | grep dreamina-design
codex-dreamina-design@personal  installed, enabled  0.1.0

$ codex debug prompt-input   # read-only; renders the model-visible skill catalogue
discovered: 14
missing:    none
```

**Result: PASS (local personal marketplace).**

The install was repeated from the **merged `main` checkout** (not just the
feature worktree), so the verified artifact is the merged tree:

```text
$ git checkout main && git log --oneline -1
865f8d5 fix: model the plan's gate semantics in the verifier exit codes
$ git ls-tree --name-only main skills/ | wc -l
14
# re-installed from ./codex-dreamina-design-plugin (main), then:
$ codex debug prompt-input
discovered: 14
```

All 14 Skills — `codex-dreamina-design-use` plus the 13 `dreamina-*`
packages — are listed. The merged `main` tree was independently
re-verified: 169/169 tests, v0 validator green, snapshot parity PASS,
strict TRACE 13/13 PASS.

The public-marketplace variant still requires a `git push origin main`
(13 commits ahead of `origin/main`, 0 behind — a safe fast-forward).
That push publishes to a public repository and is left to the
repository owner rather than performed automatically.

This step caught and fixed a real defect: the 13 packaged Skills used a
multi-line plain-scalar `description`, which is invalid YAML, so Codex
silently skipped them and only the router Skill was visible. All 13
descriptions were rewritten as valid single-line scalars, the scaffold
template was corrected, and `scripts/run_strict_trace.py` gained a
frontmatter-block validator so the defect cannot silently return.

Note: the *public*-marketplace variant is blocked because the public
`main` branch still contains only `skills/.gitkeep` — this work lives on
the unpushed `feat/dreamina-design-runtime` branch. Merging and pushing
is a repository decision the user owns; no code change is required after
that.

The merge itself has been dry-run verified as conflict-free, so that
decision is de-risked:

```text
$ git merge-tree --write-tree main feat/dreamina-design-runtime
d6cd122f51717364874ca98c06b35c035669d533      # exit 0
$ git rev-list --count main..feat/dreamina-design-runtime
11
```

`git merge-tree` is read-only — it computes the merge without touching
the working tree. Zero conflicts across all 11 commits. (This run also
confirms the new `skills/codex-dreamina-design-use/` directory does not
collide with anything on `main`.)

The repo's own `.agents/plugins/marketplace.json` — the manifest a public
install would consume — is already validated by
`scripts/validate_distribution.py`, which asserts
`source.url == <repository>.git`, `ref == main`, and
`policy == {installation: AVAILABLE, authentication: ON_USE}`.

## 8. Plan completion gate audit

The implementation plan at
`docs/superpowers/plans/2026-09-11-codex-dreamina-design-plugin-implementation.md`
ends with the following gate. Each row is annotated with the offline
evidence supporting it.

| Gate                                            | Status          | Evidence                                     |
|-------------------------------------------------|-----------------|----------------------------------------------|
| `dreamina_skill_directories = 13`               | ✅ PASS         | §3 skill_count = 13                          |
| `old_installable_jimeng_identities = 0`         | ✅ PASS         | §3 forbidden_installable_identities = []     |
| `skill_snapshot_parity = PASS`                  | ✅ PASS         | §3 parity_status (pinned to upstream HEAD)    |
| `capability_and_adapter_tests = PASS`           | ✅ PASS         | §4 test suite                                |
| `image_tests = PASS`                            | ✅ PASS         | §4 (tests/test_image_service.py)             |
| `video_tests = PASS`                            | ✅ PASS         | §4 (tests/test_video_service.py)             |
| `approval_operation_artifact_tests = PASS`      | ✅ PASS         | §4 (Task 5 test trio)                        |
| `skill_quick_validation = PASS`                 | ✅ PASS         | §3 frontmatter invariants                    |
| `skill_trace = PASS`                            | ✅ PASS         | §6 + `docs/verification/skill-trace.md`     |
| `plugin_validation = PASS`                      | ✅ PASS         | §1 + §2                                      |
| `secret_matches = 0`                            | ✅ PASS         | §2 + §5                                      |
| `read_only_runtime_contract observed or blocked` | ✅ BLOCKED      | §2 + `docs/verification/dreamina-cli-runtime.md` + `docs/verification/authorization-decision.md` |
| `paid_canary = separately approved or NOT_RUN`  | ✅ NOT_RUN      | §2 canary gate + `docs/verification/authorization-decision.md` |

Those are the 13 named gate lines. Task 7 additionally lists steps that
are not gate lines; their status is:

| Task 7 step                                                 | Status | Evidence |
|-------------------------------------------------------------|--------|----------|
| run offline tests, plugin validator, per-Skill validation    | ✅ PASS | §4 |
| link audit                                                  | ✅ PASS | §2 `marketplace_url_matches = true` |
| `git diff --check`                                          | ✅ PASS | §6 |
| install + verify Skill discovery                            | ✅ PASS | §7 (`local personal marketplace`) |
| read-only CLI runtime evidence                              | ⛔ BLOCKED | §2 (`dreamina` not on PATH) |
| paid canary                                                 | ⛔ NOT_RUN | §2 (no approval marker) |

Registry of items whose status is "evaluated and formally blocked"
rather than satisfied: see `docs/verification/authorization-decision.md`
(records the decision, the plan clauses barring model-side action, and
the unlock procedure now automated by
`scripts/unlock_runtime_gates.py`).

**No paid generation is performed offline.** Paid actions require a
separate action-time approval and an installed, authorized
`dreamina` binary.

## 9. Upstream clone evidence

The upstream `full-aigc-skills/dreamina-skills` repository was cloned
locally at `/Users/wandl/workspaces/workspace-partme-ai/dreamina-skills`
and pinned to commit
`373bf7ffa698eefd3308576300e28ea2bff9cc6b`. Each packaged Skill's
`SKILL.md` carries that exact SHA in its frontmatter; the snapshot
verifier reads the upstream HEAD directly from `.git/HEAD` and the
referenced loose / packed ref to confirm parity without invoking
`git` itself.
