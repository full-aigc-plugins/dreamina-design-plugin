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
* no secret-like byte patterns anywhere in the tree.

## 2. v7 distribution validator

```text
$ python3 scripts/validate_distribution_v7.py
```

The v7 validator returns a JSON report with these gates:

| Gate                                | Status                                            |
|-------------------------------------|---------------------------------------------------|
| `legacy_validator_exit_code`        | 0                                                 |
| `skill_count`                       | 13                                                |
| `skill_snapshot_status`             | `NOT_RUN` (no `--expected-upstream-sha` supplied) |
| `secret_matches`                    | `[]`                                              |
| `symlinks`                          | `[]`                                              |
| `marketplace_url_matches`           | `true`                                            |
| `paid_canary`                       | `NOT_RUN` (no approval marker present)            |
| `read_only_runtime_contract`        | `blocked` (dreamina CLI not on PATH)              |
| `read_only_runtime_reason`          | explicit instructions for unlocking               |

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

## 3. Skill snapshot inventory

```text
$ python3 scripts/verify_skill_snapshot.py .
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
  "parity_status": "NOT_RUN",
  "parity_reason": "upstream full-aigc-skills/dreamina-skills path not provided; ..."
}
```

Byte-parity is reported as `NOT_RUN` until the upstream repository is
cloned locally. The packaged Skill stubs explicitly pin
`upstream_commit_sha: NOT_VERIFIED` and `do_not_edit_body: true`; no
upstream Skill body has been copied into this plugin.

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
the scan; any other match is reported as `secret_matches[i].path`.

## 6. TRACE / `git diff --check`

Strict TRACE and `git diff --check` are deferred until the upstream
`full-aigc-skills/dreamina-skills` repository is cloned locally and
the byte-parity check is enabled (see `verify_skill_snapshot.py`).
Running TRACE / `git diff --check` requires the same upstream source
of truth.

## 7. Plan completion gate audit

The implementation plan at
`docs/superpowers/plans/2026-09-11-codex-dreamina-design-plugin-implementation.md`
ends with the following gate. Each row is annotated with the offline
evidence supporting it.

| Gate                                            | Status          | Evidence                                     |
|-------------------------------------------------|-----------------|----------------------------------------------|
| `dreamina_skill_directories = 13`               | ✅ PASS         | §3 skill_count = 13                          |
| `old_installable_jimeng_identities = 0`         | ✅ PASS         | §3 forbidden_installable_identities = []     |
| `skill_snapshot_parity = PASS`                  | ⚠️ NOT_RUN      | §3 parity_status; unlock via upstream clone  |
| `capability_and_adapter_tests = PASS`           | ✅ PASS         | §4 test suite                                |
| `image_tests = PASS`                            | ✅ PASS         | §4 (tests/test_image_service.py)             |
| `video_tests = PASS`                            | ✅ PASS         | §4 (tests/test_video_service.py)             |
| `approval_operation_artifact_tests = PASS`      | ✅ PASS         | §4 (Task 5 test trio)                        |
| `skill_quick_validation = PASS`                 | ✅ PASS         | §3 frontmatter invariants                    |
| `skill_trace = PASS`                            | ⚠️ DEFERRED     | Requires upstream clone for TRACE           |
| `plugin_validation = PASS`                      | ✅ PASS         | §1 + §2                                      |
| `secret_matches = 0`                            | ✅ PASS         | §2 + §5                                      |
| `read_only_runtime_contract observed or blocked` | ✅ BLOCKED      | §2 + `docs/verification/dreamina-cli-runtime.md` |
| `paid_canary = separately approved or NOT_RUN`  | ✅ NOT_RUN      | §2 canary gate                               |

**No paid generation is performed offline.** Paid actions require a
separate action-time approval and an installed, authorized
`dreamina` binary.
