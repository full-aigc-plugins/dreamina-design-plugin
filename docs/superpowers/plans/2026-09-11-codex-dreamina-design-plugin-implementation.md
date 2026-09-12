# Codex Dreamina Design Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build current, safe Dreamina image/video workflows for Codex.

**Architecture:** Migrated Dreamina Skills call a strict CLI adapter backed by capability, approval, operation, and artifact contracts.

**Tech Stack:** Codex plugin, Agent Skills, Python, JSON Schema, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-09-11-codex-dreamina-design-plugin-design.md`

**Completion audit (2026-09-12):** all seven tasks are implemented and the
checkboxes below were reconciled against source, commit history, 203 passing
offline tests, distribution validation, strict upstream TRACE, public Skill
discovery evidence, and a fresh read-only CLI capture. The paid canary remains
`NOT_RUN`, which is an explicit accepted branch of the completion gate and did
not authorize generation. The packaged Skills remain pinned to verified source
commit `300bfc1`; the 13 complete packaged Skill trees are byte-identical to
that pinned upstream commit.

## Constraints

- ID `codex-dreamina-design`; no `jimeng-*` installable identities.
- Runtime CLI contract is authoritative; no paid CI calls.
- Approval is request-bound; async uncertainty never causes resubmission.

## Foundation baseline completed 2026-09-12

The `jimeng-skills` to `dreamina-skills` migration is published, and this repository now contains the validated `codex-dreamina-design` compatibility manifest, URL marketplace entry, Apache-2.0/legal files, transparent brand assets, implementation directories, distribution validator, and RED/GREEN foundation tests. Executors must preserve these files and begin remaining work with runtime contracts and the thin orchestration Skill; they must not repeat the migration or create the previously rejected overlapping 14-Skill design.

### Task 1: Migrate dreamina-skills
- [x] Write a failing inventory test covering all 13 old directories, frontmatter names, links, README and install commands.
- [x] Rename the GitHub/local repository and each `jimeng-*` directory through an explicit mapping ledger.
- [x] Update references and current CLI contract snapshots; run 13 quick validations and TRACE.
- [x] Prove zero installable `jimeng-*` identities; commit and publish the migration.

### Task 2: Plugin contracts and adapter
- [x] Write failing manifest and closed-schema tests.
- [x] Add capability/generation/approval/operation/artifact schemas and argv-only CLI adapter.
- [x] Test errors and read-only discovery; commit `feat: define Dreamina Design runtime`.

### Task 3: Image workflows
- [x] Baseline failures for unsupported model/resolution, reference scope, batch count and blind retry.
- [x] Package and validate text-to-image and image-to-image Skills.
- [x] Run offline fixtures; commit `feat: add Dreamina image workflows`.

### Task 4: Video workflows
- [x] Write failing tests for text/image/frame/multimodal modes, web prerequisite, ratio, duration and resolution discovery.
- [x] Package and validate video Skills using live capability snapshots.
- [x] Run fixtures; commit `feat: add Dreamina video workflows`.

### Task 5: Async recovery and downloads
- [x] Write failing state-machine tests including restart and unknown submission outcomes.
- [x] Implement atomic non-secret ledger, bounded polling and artifact verification.
- [x] Prove no timeout resubmission; commit `feat: recover Dreamina design tasks`.

### Task 6: Distribution and acceptance
- [x] Add repository marketplace and failing distribution tests.
- [x] Run quick validation, TRACE, links, secret scans and plugin validation.
- [x] Run only read-only CLI runtime checks; keep credit canary separately approved.
- [x] Commit `test: verify Dreamina Design distribution`.

---

## Detailed executor contract

### Task 1 — migrate the shared Skill source of truth

**Source repository:** `full-aigc-skills/jimeng-skills` (current local repository must be inspected again before execution).

**Target repository:** `full-aigc-skills/dreamina-skills`; local directory basename `dreamina-skills`.

**Exact identity map:**

| Old | New |
|---|---|
| `jimeng-cli-image2image` | `dreamina-cli-image2image` |
| `jimeng-cli-image2video` | `dreamina-cli-image2video` |
| `jimeng-cli-text2image` | `dreamina-cli-text2image` |
| `jimeng-cli-text2video` | `dreamina-cli-text2video` |
| `jimeng-opencli-image2image` | `dreamina-opencli-image2image` |
| `jimeng-opencli-image2video` | `dreamina-opencli-image2video` |
| `jimeng-opencli-text2image` | `dreamina-opencli-text2image` |
| `jimeng-opencli-text2video` | `dreamina-opencli-text2video` |
| `jimeng-prompt-image2image` | `dreamina-prompt-image2image` |
| `jimeng-prompt-image2video` | `dreamina-prompt-image2video` |
| `jimeng-prompt-text2image` | `dreamina-prompt-text2image` |
| `jimeng-prompt-text2video` | `dreamina-prompt-text2video` |
| `dreamina-cli` | `dreamina-cli` |

**Files:** rename the 12 directories; modify all `SKILL.md`, references, examples, READMEs, badges, links and installation commands; create `docs/migration/jimeng-to-dreamina.md`, `scripts/verify_skill_inventory.py`, `tests/test_identity_migration.py`.

- [x] Record branch/status/remote SHA and enumerate every tracked path containing `jimeng-`; preserve the pre-migration list as test input.
- [x] Write RED tests asserting 13 target directories, directory/frontmatter equality, zero old installable identities, target GitHub URLs and README install command.
- [x] Create and obtain review for an explicit repository/branch rename checkpoint before GitHub rename; do not combine it with Skill edits if the worktree is dirty.
- [x] Rename directories through the table, then update frontmatter and local cross-Skill references.
- [x] Update public GitHub links from `full-aigc-skills/jimeng-skills` to `full-aigc-skills/dreamina-skills`; product prose may retain “即梦/Jimeng” only when explaining branding or migration.
- [x] Synchronize installed CLI evidence to current version/help/schema. When the current CLI has no `schema` command, capture its top-level and per-command help as the authoritative command-help snapshot. Required known deltas include Seedream 5.0 Pro `1.5k`, Seedance 2.5 `1080p`, and video ratio control; do not infer unsupported flags from documentation alone.
- [x] Run each Skill's pre-migration scenario, then quick validation and strict TRACE after modification; no batch “one result covers all Skills”.
- [x] Run link, placeholder, secret, inventory and `git diff --check` gates.
- [x] Commit content migration, rename the GitHub repository, update origin, rename the local directory, then prove local/tracking/remote SHA equality.

### Task 2 — plugin and runtime contracts

**Files:** `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`, `schemas/capability_snapshot.schema.json`, `schemas/generation_request.schema.json`, `schemas/approval_receipt.schema.json`, `schemas/operation_receipt.schema.json`, `schemas/artifact_receipt.schema.json`, `scripts/dreamina_adapter.py`, `tests/test_contracts.py`, `tests/test_dreamina_adapter.py`.

```python
@dataclass(frozen=True)
class DreaminaResult:
    exit_code: int
    payload: dict | list | None
    error_code: str | None
    submit_id: str | None

def run_dreamina(args: list[str], timeout_seconds: int) -> DreaminaResult: ...
def capability_snapshot() -> dict: ...
```

- [x] RED-test ID `codex-dreamina-design`, closed schemas, no credential fields, and request/approval fingerprint relationships.
- [x] RED-test missing CLI, auth/permission errors, invalid JSON, upgrade notice, timeout, stdout/stderr separation and output limits.
- [x] Implement argv-only execution and live schema capability parsing with a current command-help fallback; do not install or log in.
- [x] Run focused tests and plugin validation; commit.

### Task 3 — image request workflows

**Files:** `scripts/image_service.py`, `tests/test_image_service.py`, packaged `dreamina-cli-text2image`, `dreamina-cli-image2image`, `dreamina-prompt-text2image`, `dreamina-prompt-image2image` Skills.

- [x] RED-test text/image modes, 1–10 batch count discovery, required `resolution_type`, paired width/height, width/height versus ratio exclusivity, unsupported model tokens, reference scope/type/size and request fingerprints.
- [x] Build requests only from the current capability snapshot; prompt Skills may improve prose but cannot add unsupported parameters.
- [x] Require approval for every generation; submit a batch once and preserve per-item results.
- [x] Validate each packaged Skill and its routing scenarios; commit.

### Task 4 — video request workflows

**Files:** `scripts/video_service.py`, `tests/test_video_service.py`, packaged text/image video Prompt and CLI Skills.

- [x] RED-test text2video, image2video, frames2video and multimodal2video mode selection.
- [x] Cover current model/resolution/ratio/duration discovery, explicit `video_resolution`, Seedance 2.5 480P/720P/1080P, 4–30 seconds, and 2–30 second audiovisual/pure-audio reference constraints only when present in the observed CLI contract.
- [x] Report the first-web-video prerequisite as `WEB_PREREQUISITE_REQUIRED`; do not attempt bypass.
- [x] Bind approval to prompt, all references, model, resolution, ratio and duration; commit after fixtures and Skill checks pass.

### Task 5 — sessions, operations and artifacts

**Files:** `scripts/approval_guard.py`, `scripts/operation_ledger.py`, `scripts/artifact_service.py`, `tests/test_approval_guard.py`, `tests/test_operation_ledger.py`, `tests/test_artifact_service.py`.

- [x] RED-test session create/list/select/delete boundaries, request changes, approval replay/expiry, submit ID persistence, terminal/unknown states, process restart, cancel support discovery and download failure.
- [x] Persist non-secret receipts atomically; never store tokens, full prompts marked private, or account snapshots.
- [x] On ambiguous submission, query by submit ID/history before any new submission.
- [x] Validate downloaded bytes, media metadata and checksum before completion; commit.

### Task 6 — router and packaged Skill quality

**Files:** `skills/codex-dreamina-design-use/SKILL.md`, packaged Skill snapshot, `tests/scenarios/`, `scripts/verify_skill_snapshot.py`.

- [x] Baseline ambiguous routing, hard-coded catalogs, silent login, web-prerequisite bypass, approval reuse and blind retry scenarios.
- [x] Create a thin router that chooses existing migrated Skills and never duplicates their prompt/CLI instructions.
- [x] Package every complete Skill tree and prove byte parity against an explicit verified `dreamina-skills` source SHA.
- [x] Run quick validation, strict TRACE and forward scenarios for every packaged entry.
- [x] Commit only after zero old `jimeng-*` installable identities remain.

### Task 7 — distribution and runtime evidence

**Files:** `scripts/validate_distribution.py`, `tests/test_distribution.py`, `docs/verification/offline.md`, `docs/verification/dreamina-cli-runtime.md`.

- [x] RED-test repository source, identity, version, Skill snapshot SHA, links, licenses, no symlinks/caches and secret patterns.
- [x] Run all offline tests, plugin validator, per-Skill validation, TRACE, link audit and `git diff --check`.
- [x] Install from the public marketplace and verify Skill discovery in a fresh Codex task.
- [x] With the existing authorized login, record only version/help/command-help capability evidence and non-secret account readiness; do not perform generation.
- [x] Treat a paid image/video canary as a separate action-time approval and preserve `NOT_RUN` when absent.
- [x] Commit evidence and stop for integration choice.

## Completion gate

```text
dreamina_skill_directories = 13
old_installable_jimeng_identities = 0
skill_snapshot_parity = PASS
capability_and_adapter_tests = PASS
image_tests = PASS
video_tests = PASS
approval_operation_artifact_tests = PASS
skill_quick_validation = PASS
skill_trace = PASS
plugin_validation = PASS
secret_matches = 0
read_only_runtime_contract = observed or explicitly blocked
paid_canary = separately approved or NOT_RUN
```
