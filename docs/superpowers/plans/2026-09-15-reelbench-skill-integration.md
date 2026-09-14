# Dreamina ReelBench Skill Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package ReelBench as `dreamina-video-shots` and `dreamina-video-sync` without changing its workflows, then expose both through trusted, versioned Dreamina MCP/Harness capabilities.

**Architecture:** Vendor the two pinned upstream trees with only a verified directory/frontmatter-name transformation. A Python adapter runs their unchanged Node entrypoints behind enrolled-tool, private-project, bounded-process, immutable-evidence, and recovery boundaries; two additive MCP tools attach the results to existing Dreamina video projects without replacing native analysis or final composition.

**Tech Stack:** Python 3 standard library, MCP stdio JSON-RPC, Node.js 18+ upstream scripts, FFmpeg/ffprobe, Chrome/Chromium/Edge, JSON Schema Draft 2020-12, unittest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-15-reelbench-skill-integration-design.md`

## Global Constraints

- Pin ReelBench to `18f2f63987337df0975a89973d38d50f3231ee31` unless a new revision is explicitly approved before Task 1 begins.
- Package upstream `video-shots` as `dreamina-video-shots` and `video-sync` as `dreamina-video-sync`.
- The only upstream-content delta is the `name` value in each packaged `SKILL.md`; scripts, references, assets, examples, README files, and all other bytes remain identical.
- Do not bundle ReelBench demo media, Node, FFmpeg, ffprobe, Chrome, credentials, or private project artifacts.
- Existing 0.4.0 MCP tool schemas and the existing 17 Skill identities remain byte-compatible; the new target is 23 MCP tools and 19 Skills.
- Native Dreamina analysis remains authoritative for paid generation; ReelBench mismatches enter `manual_review` and never overwrite measured facts.
- `dreamina-video-sync` output is review evidence, never a generated shot, final composition, or export gate.
- Every process is argv-only, `shell=False`, bounded in time/output, process-group terminated, and tied to pinned executable identity.
- All project files use private descriptor-relative paths, `O_NOFOLLOW`, exact owner/mode checks, bounded streaming I/O, no-replace publication, file/directory fsync, and typed indeterminate recovery.
- No paid Dreamina request, package publication, or dependency installation occurs without fresh action-time authorization.

---

## File map

- `skills/dreamina-video-shots/`, `skills/dreamina-video-sync/`: transformed packaged upstream Skill trees.
- `upstream/reelbench.lock.json`: pinned revision and per-file upstream/package digests.
- `scripts/verify_reelbench_snapshot.py`: deterministic alias-aware parity verifier.
- `schemas/reelbench_evidence.schema.json`: closed immutable action/evidence receipt.
- `schemas/reelbench_comparison.schema.json`: native/ReelBench comparison receipt.
- `scripts/reelbench_adapter.py`: fixed upstream command construction and bounded result parsing.
- `scripts/reelbench_project_service.py`: project versioning, artifacts, comparison, and recovery.
- `scripts/reelbench_sync_service.py`: panel/review-video execution and verification.
- `scripts/trusted_media_tools.py`: additive Node/browser trusted-tool identities.
- `scripts/video_project_mcp.py`: two additive project tool contracts and handlers.
- `scripts/router_skill.py`: non-shadowing shot-analysis/review-video routes.
- `scripts/run_reference_video_acceptance.py`: local no-paid ReelBench acceptance hooks.
- `tests/test_reelbench_snapshot.py`, `tests/test_reelbench_adapter.py`, `tests/test_reelbench_project_service.py`, `tests/test_reelbench_sync_service.py`: focused unit/security tests.
- `tests/test_reelbench_mcp.py`, `tests/test_reelbench_acceptance.py`: public Harness and no-paid integration tests.
- `.codex-plugin/plugin.json`, `.mcp.json`, `.agents/plugins/marketplace.json`, `README.md`, `README.zh-CN.md`, `THIRD_PARTY_NOTICES.md`: 0.5.0 distribution metadata and documentation.

---

### Task 1: Vendor and lock the two renamed upstream Skills

**Files:**
- Create: `skills/dreamina-video-shots/**`
- Create: `skills/dreamina-video-sync/**`
- Create: `upstream/reelbench.lock.json`
- Create: `scripts/verify_reelbench_snapshot.py`
- Create: `tests/test_reelbench_snapshot.py`
- Modify: `THIRD_PARTY_NOTICES.md`

**Interfaces:**
- Consumes: Git tree `eternityspring/reelbench-skills@18f2f63987337df0975a89973d38d50f3231ee31`.
- Produces: `verify_reelbench_snapshot(plugin_root: Path, upstream_root: Path | None = None) -> ReelBenchSnapshotReport` and two discoverable packaged Skills.

- [ ] **Step 1: Write failing alias/parity tests**

```python
def test_packaged_skill_aliases_are_the_only_allowed_delta(self):
    report = verify_reelbench_snapshot(ROOT, upstream_root=self.upstream)
    self.assertEqual(report.revision, "18f2f63987337df0975a89973d38d50f3231ee31")
    self.assertEqual(report.packaged_names, ["dreamina-video-shots", "dreamina-video-sync"])
    self.assertEqual(report.mismatches, [])

def test_non_name_edit_or_unlisted_file_is_rejected(self):
    self.mutate("skills/dreamina-video-shots/scripts/video-shots.mjs")
    self.assertIn("scripts/video-shots.mjs", verify_reelbench_snapshot(self.root, self.upstream).mismatches)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_reelbench_snapshot -v`  
Expected: FAIL because the packaged trees, lock, and verifier do not exist.

- [ ] **Step 3: Copy pinned trees and apply only the name transformation**

Copy every file under upstream `skills/video-shots` and `skills/video-sync` into the aliased directories. Change only these two frontmatter lines:

```yaml
name: dreamina-video-shots
name: dreamina-video-sync
```

Generate `upstream/reelbench.lock.json` with this closed top-level shape:

```json
{
  "schema_version": "1.0.0",
  "source": "https://github.com/eternityspring/reelbench-skills.git",
  "revision": "18f2f63987337df0975a89973d38d50f3231ee31",
  "aliases": {"video-shots": "dreamina-video-shots", "video-sync": "dreamina-video-sync"},
  "files": {}
}
```

Each `files` entry records `git_blob`, `upstream_sha256`, and `packaged_sha256`. For `SKILL.md`, the verifier reconstructs the permitted single-line alias before comparing; no generic whitespace normalization is allowed.

- [ ] **Step 4: Run upstream and plugin parity tests**

Run:

```bash
node skills/dreamina-video-shots/scripts/selftest.mjs
node skills/dreamina-video-sync/scripts/selftest.mjs
python3 -m unittest tests.test_reelbench_snapshot tests.test_verify_skill_snapshot tests.test_distribution -v
```

Expected: both unchanged upstream self-tests PASS; alias verifier reports zero mismatches; existing 13-Skill Dreamina snapshot remains unchanged.

- [ ] **Step 5: Commit**

```bash
git add skills/dreamina-video-shots skills/dreamina-video-sync upstream/reelbench.lock.json scripts/verify_reelbench_snapshot.py tests/test_reelbench_snapshot.py THIRD_PARTY_NOTICES.md
git commit -m "feat: package pinned ReelBench skills"
```

---

### Task 2: Add trusted Node/browser identities and closed evidence contracts

**Files:**
- Modify: `scripts/trusted_media_tools.py`
- Modify: `tests/test_trusted_media_tools.py`
- Create: `schemas/reelbench_evidence.schema.json`
- Create: `schemas/reelbench_comparison.schema.json`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Consumes: existing `TrustedMediaToolStore` enrollment and native confirmation.
- Produces: tool kinds `node` and `browser`, `resolve_verified(kind) -> TrustedExecutable`, and closed evidence/comparison schemas.

- [ ] **Step 1: Write failing trusted-tool and schema tests**

```python
def test_node_and_browser_are_verified_by_exact_identity(self):
    node = self.store.enroll("node", self.node_path)
    browser = self.store.enroll("browser", self.chrome_path)
    self.assertEqual(self.store.resolve_verified("node").sha256, node.sha256)
    self.assertEqual(self.store.resolve_verified("browser").sha256, browser.sha256)

def test_reelbench_receipts_are_closed_and_distinguish_skipped(self):
    validate_contract(self.valid_receipt, "reelbench_evidence.schema.json")
    with self.assertRaises(ContractValidationError):
        validate_contract({**self.valid_receipt, "unknown": True}, "reelbench_evidence.schema.json")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_trusted_media_tools tests.test_contracts -v`  
Expected: FAIL because the tool kinds and schemas are absent.

- [ ] **Step 3: Implement the minimal additive trust/schema contract**

Add `node` as a user-enrolled executable. Add `browser` with an explicit system-tool policy limited to absolute Chrome/Chromium/Edge application executables; pin path, owner, mode, device, inode, size, and SHA-256. Root ownership is allowed only for exact system/application paths and is never inferred from basename.

Define `reelbench_evidence` fields for project/source/action/parent/upstream/tool identities/argv fingerprint/gates/artifacts/timestamps/fingerprint. Define `reelbench_comparison` fields for exact native and ReelBench versions, tolerances, per-domain verdicts, overall `matched|manual_review`, and fingerprint.

- [ ] **Step 4: Run security tests**

Run: `python3 -m unittest tests.test_trusted_media_tools tests.test_contracts tests.test_native_approval -v`  
Expected: PASS for exact identities; symlink, owner/mode change, executable replacement, and unapproved system paths fail closed.

- [ ] **Step 5: Commit**

```bash
git add scripts/trusted_media_tools.py tests/test_trusted_media_tools.py schemas/reelbench_evidence.schema.json schemas/reelbench_comparison.schema.json tests/test_contracts.py
git commit -m "feat: define trusted ReelBench evidence"
```

---

### Task 3: Implement the guarded `dreamina-video-shots` adapter and project versions

**Files:**
- Create: `scripts/reelbench_adapter.py`
- Create: `scripts/reelbench_project_service.py`
- Create: `tests/test_reelbench_adapter.py`
- Create: `tests/test_reelbench_project_service.py`
- Modify: `scripts/video_project_store.py`
- Modify: `tests/test_video_project_store.py`

**Interfaces:**
- Consumes: pinned Skill path, verified Node/FFmpeg/ffprobe, exact project source receipt.
- Produces: `ReelBenchAdapter.seed/evidence/validate/render`, and `ReelBenchProjectService.run(project_id, action, ...) -> ReelBenchEvidence`.

- [ ] **Step 1: Write failing argv, bounds, and version tests**

```python
def test_seed_uses_pinned_script_and_fixed_argv_without_shell(self):
    result = self.adapter.seed(source=self.source_fd_path, output=self.output, threshold=0.3, title="Reference")
    self.assertEqual(result.argv[1:3], [str(self.shots_script), "seed"])
    self.assertFalse(result.shell)

def test_repeated_action_creates_new_version_without_overwriting(self):
    first = self.service.run(self.project_id, action="seed", expected_parent=None)
    second = self.service.run(self.project_id, action="seed", expected_parent=first["version"])
    self.assertEqual((first["version"], second["version"]), ("v001", "v002"))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_reelbench_adapter tests.test_reelbench_project_service -v`  
Expected: FAIL because both modules are absent.

- [ ] **Step 3: Implement fixed commands and immutable evidence**

Expose only these command builders:

```python
seed(source, shots, track, threshold, title)
frames(shots, source, frames_dir)
sheet(shots, frames_dir, sheets_dir, pick)
validate(shots, track, frames_dir)
render(shots, track, frames_dir, source, mode)
```

All paths are service-created project paths. JSON stdout/stderr are streamed with explicit caps; file counts, shot counts, duration, frames, sheets, report sizes, and recursion depth are bounded. Parse gate lines into exact `PASS|FAIL|SKIPPED`; never convert `SKIPPED` to success.

Persist each action through `VideoProjectStore.write_version(..., family="reelbench_evidence")`. Bind every artifact digest and the upstream/tool identities. Reconcile `VersionCommitIndeterminateError` only against its exact fingerprint/version.

- [ ] **Step 4: Run adversarial and upstream tests**

Run:

```bash
python3 -m unittest tests.test_reelbench_adapter tests.test_reelbench_project_service tests.test_video_project_store -v
node skills/dreamina-video-shots/scripts/selftest.mjs
```

Expected: PASS, including malformed/oversized JSON, source replacement, output collision, short write, timeout, process cleanup, skipped gates, and restart recovery.

- [ ] **Step 5: Commit**

```bash
git add scripts/reelbench_adapter.py scripts/reelbench_project_service.py scripts/video_project_store.py tests/test_reelbench_adapter.py tests/test_reelbench_project_service.py tests/test_video_project_store.py
git commit -m "feat: run versioned ReelBench shot analysis"
```

---

### Task 4: Compare ReelBench evidence with native analysis

**Files:**
- Modify: `scripts/reelbench_project_service.py`
- Modify: `scripts/shot_analysis_service.py`
- Modify: `tests/test_reelbench_project_service.py`
- Modify: `tests/test_shot_analysis_service.py`

**Interfaces:**
- Consumes: exact `analysis_version` and validated `reelbench_evidence` version for the same source digest.
- Produces: `compare_native(project_id, analysis_version, reelbench_version) -> ReelBenchComparison` and optional comparison binding on annotation/redesign.

- [ ] **Step 1: Write failing comparison tests**

```python
def test_matching_timelines_produce_corroborating_receipt(self):
    result = self.service.compare_native(self.project_id, "v001", "v001")
    self.assertEqual(result["overall"], "matched")

def test_source_or_boundary_mismatch_requires_manual_review_without_mutation(self):
    before = self.store.read_version(self.project_id, "analysis", "v001")
    result = self.service.compare_native(self.project_id, "v001", "v002")
    self.assertEqual(result["overall"], "manual_review")
    self.assertEqual(self.store.read_version(self.project_id, "analysis", "v001"), before)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_reelbench_project_service tests.test_shot_analysis_service -v`  
Expected: FAIL because no comparison interface exists.

- [ ] **Step 3: Implement closed comparison rules**

Compare source SHA-256, total duration, timeline continuity, shot count, boundaries within the declared tolerance, and per-shot motion evidence. Report every mismatch. The comparison may add a corroborating reference to a later annotation/redesign version, but cannot edit native analysis, copy model semantics into measured fields, or turn a failed native gate into PASS.

- [ ] **Step 4: Run comparison and compatibility tests**

Run: `python3 -m unittest tests.test_reelbench_project_service tests.test_shot_analysis_service tests.test_reference_video_compatibility -v`  
Expected: PASS; reordered/missing shots, foreign source, changed ReelBench receipt, stale native version, and tolerance-boundary cases fail closed.

- [ ] **Step 5: Commit**

```bash
git add scripts/reelbench_project_service.py scripts/shot_analysis_service.py tests/test_reelbench_project_service.py tests/test_shot_analysis_service.py
git commit -m "feat: cross-check ReelBench and native analysis"
```

---

### Task 5: Implement guarded synchronized review-video generation

**Files:**
- Create: `scripts/reelbench_sync_service.py`
- Create: `tests/test_reelbench_sync_service.py`
- Modify: `scripts/reelbench_adapter.py`
- Modify: `tests/test_reelbench_adapter.py`

**Interfaces:**
- Consumes: validated ReelBench version, exact source receipt, verified Node/browser/FFmpeg/ffprobe, review audio policy.
- Produces: `plan`, `panels`, `export`, `verify`, and immutable `reelbench_sync` evidence versions.

- [ ] **Step 1: Write failing plan/export/identity tests**

```python
def test_portrait_and_landscape_layouts_preserve_source_aspect(self):
    self.assertEqual(self.service.plan(self.landscape)["layout"], "vertical-stack")
    self.assertEqual(self.service.plan(self.portrait)["layout"], "horizontal-stack")

def test_review_video_cannot_be_used_as_final_composition(self):
    receipt = self.service.export(self.inputs)
    self.assertEqual(receipt["artifact_role"], "synchronized_review")
    with self.assertRaises(FinalMediaVerificationError):
        self.final_verifier.accept_generated_shot(receipt)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_reelbench_sync_service tests.test_reelbench_adapter -v`  
Expected: FAIL because the sync service is absent.

- [ ] **Step 3: Implement unchanged upstream plan/panels/export behind the guard**

Build fixed commands for `plan`, `panels`, and `export`. Validate `shots.json` source/duration, layout JSON, exactly three panel images, Chrome identity, output containment, original-aspect geometry, codec, duration, and audio policy. Publish the MP4 under role `synchronized_review` and write sampled shot/highlight alignment evidence.

Chrome absence returns `BLOCKED_MISSING_TRUSTED_BROWSER`. A source without permitted audio is passed through the explicitly selected silent review policy; it is never silently upgraded to preserved audio.

- [ ] **Step 4: Run security, self-test, and real local fixture tests**

Run:

```bash
python3 -m unittest tests.test_reelbench_sync_service tests.test_reelbench_adapter tests.test_final_media_service -v
node skills/dreamina-video-sync/scripts/selftest.mjs
```

Expected: PASS for fixed commands and synthetic runner; local real fixture runs only when already-enrolled Node/FFmpeg/browser prerequisites exist and otherwise reports a typed blocked result.

- [ ] **Step 5: Commit**

```bash
git add scripts/reelbench_sync_service.py scripts/reelbench_adapter.py tests/test_reelbench_sync_service.py tests/test_reelbench_adapter.py
git commit -m "feat: create synchronized ReelBench review videos"
```

---

### Task 6: Expose MCP tools and route all three video intents

**Files:**
- Modify: `scripts/video_project_mcp.py`
- Modify: `scripts/dreamina_mcp_server.py`
- Modify: `.mcp.json`
- Modify: `scripts/router_skill.py`
- Modify: `skills/dreamina-design-use/SKILL.md`
- Modify: `skills/dreamina-video-production/SKILL.md`
- Create: `tests/test_reelbench_mcp.py`
- Modify: `tests/test_video_project_mcp.py`
- Modify: `tests/test_router_skill.py`
- Modify: `tests/test_video_project_skills.py`

**Interfaces:**
- Consumes: Tasks 3–5 services.
- Produces: MCP tools `dreamina_reelbench_shots`, `dreamina_reelbench_sync`, and router modes `shot_breakdown`, `synchronized_review`, `reference`.

- [ ] **Step 1: Write failing public-contract tests**

```python
def test_two_additive_tools_have_closed_actions(self):
    tools = {item["name"]: item for item in all_tool_definitions()}
    self.assertEqual(set(tools["dreamina_reelbench_shots"]["action"]["enum"]),
                     {"seed", "evidence", "validate", "render", "compare_native", "status"})
    self.assertEqual(set(tools["dreamina_reelbench_sync"]["action"]["enum"]),
                     {"plan", "panels", "export", "verify", "status"})

def test_router_uses_dreamina_aliases_without_shadowing_direct_video(self):
    self.assertEqual(Router().route(intent="video_project", mode="shot_breakdown"), "dreamina-video-shots")
    self.assertEqual(Router().route(intent="video_project", mode="synchronized_review"), "dreamina-video-sync")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_reelbench_mcp tests.test_video_project_mcp tests.test_router_skill -v`  
Expected: FAIL because tool definitions and routes are absent.

- [ ] **Step 3: Implement thin handlers and routing**

Handlers validate closed arguments and delegate to one service method. `status`, `plan`, and comparison reads are read-only; artifact-producing actions use prompt approval. Update the router/production Skills to prefer guarded MCP execution while leaving the two vendored ReelBench Skill bodies untouched.

- [ ] **Step 4: Run public API and compatibility tests**

Run:

```bash
python3 -m unittest tests.test_reelbench_mcp tests.test_video_project_mcp tests.test_dreamina_mcp_server tests.test_router_skill tests.test_reference_video_compatibility -v
```

Expected: 23 tools are discoverable; all legacy 0.3.0/0.4.0 tool schemas remain byte-identical; unknown fields/actions and path escapes are rejected before handlers run.

- [ ] **Step 5: Commit**

```bash
git add scripts/video_project_mcp.py scripts/dreamina_mcp_server.py .mcp.json scripts/router_skill.py skills/dreamina-design-use/SKILL.md skills/dreamina-video-production/SKILL.md tests/test_reelbench_mcp.py tests/test_video_project_mcp.py tests/test_router_skill.py tests/test_video_project_skills.py
git commit -m "feat: expose guarded ReelBench project tools"
```

---

### Task 7: Add no-paid end-to-end acceptance and failure recovery

**Files:**
- Modify: `scripts/run_reference_video_acceptance.py`
- Create: `tests/test_reelbench_acceptance.py`
- Modify: `tests/test_run_reference_video_acceptance.py`
- Create: `docs/verification/reelbench-local-acceptance.md`

**Interfaces:**
- Consumes: public MCP/Harness tools with a generated local fixture and already-enrolled prerequisites.
- Produces: observable acceptance gates `reelbench_shots`, `reelbench_native_comparison`, and `reelbench_sync_review`.

- [ ] **Step 1: Write failing acceptance tests**

```python
def test_no_paid_fixture_runs_both_reelbench_workflows_through_public_tools(self):
    report = self.runner.run(allow_paid=False, run_reelbench=True)
    self.assertEqual(report["gates"]["reelbench_shots"]["status"], "PASS")
    self.assertEqual(report["gates"]["reelbench_native_comparison"]["status"], "PASS")
    self.assertEqual(report["gates"]["reelbench_sync_review"]["status"], "PASS")
    self.assertEqual(self.fake_dreamina_paid_calls, [])

def test_missing_browser_is_blocked_not_passed(self):
    report = self.runner.run(allow_paid=False, run_reelbench=True)
    self.assertEqual(report["gates"]["reelbench_sync_review"]["status"], "NOT_RUN")
    self.assertIn("browser", report["gates"]["reelbench_sync_review"]["reason"])
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_reelbench_acceptance tests.test_run_reference_video_acceptance -v`  
Expected: FAIL because the gates and runner hook are absent.

- [ ] **Step 3: Implement the opt-in local project driver**

Add `--reelbench-local` as an opt-in, no-paid flag. The driver creates a synthetic local video, calls the public project/MCP surface, produces native and ReelBench analyses, compares them, renders reports, and creates/verifies the synchronized review video. It never calls quote activation or generation submission. Missing enrollment/native confirmation/browser remains `NOT_RUN` with a concrete prerequisite.

- [ ] **Step 4: Run failure injection and real-host acceptance**

Run:

```bash
python3 -m unittest tests.test_reelbench_acceptance tests.test_run_reference_video_acceptance -v
python3 scripts/run_reference_video_acceptance.py --no-paid --reelbench-local --json
```

Expected: tests PASS. The real-host command records PASS only for actions actually completed; unavailable approval/tool/browser prerequisites stay distinct `NOT_RUN`, never fabricated PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_reference_video_acceptance.py tests/test_reelbench_acceptance.py tests/test_run_reference_video_acceptance.py docs/verification/reelbench-local-acceptance.md
git commit -m "test: accept local ReelBench project workflows"
```

---

### Task 8: Release 0.5.0 metadata, installed-artifact gates, and final verification

**Files:**
- Modify: `.codex-plugin/plugin.json`
- Modify: `.agents/plugins/marketplace.json`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/verification/command-coverage.md`
- Modify: `tests/test_distribution.py`
- Modify: `tests/test_distribution_v7.py`
- Modify: `tests/test_video_project_skills.py`
- Modify: `tests/test_dreamina_mcp_server.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: all previous tasks and pinned upstream lock.
- Produces: installable Dreamina Design `0.5.0` with 19 Skills, 23 MCP tools, CI parity, and installed-runtime evidence.

- [ ] **Step 1: Write failing distribution assertions**

```python
def test_distribution_declares_23_tools_19_skills_and_reelbench_pin(self):
    report = validate_distribution(ROOT)
    self.assertEqual(report.tool_count, 23)
    self.assertEqual(report.total_skill_count, 19)
    self.assertEqual(report.reelbench_revision, "18f2f63987337df0975a89973d38d50f3231ee31")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_distribution tests.test_distribution_v7 tests.test_video_project_skills tests.test_dreamina_mcp_server -v`  
Expected: FAIL while metadata remains 0.4.0/17 Skills/21 tools.

- [ ] **Step 3: Update release metadata and CI**

Set repository and marketplace version to `0.5.0`. Document the two Skill aliases, the review-only boundary, required local tools, MCP examples, blocked prerequisites, and exact upstream revision. CI runs both upstream self-tests and `verify_reelbench_snapshot.py` in addition to the Python suite.

- [ ] **Step 4: Run complete offline and packaging verification**

Run:

```bash
node skills/dreamina-video-shots/scripts/selftest.mjs
node skills/dreamina-video-sync/scripts/selftest.mjs
python3 scripts/verify_reelbench_snapshot.py --upstream-root /Users/wandl/.agent-reach/repositories/eternityspring/reelbench-skills
python3 -m unittest discover -s tests -v
python3 scripts/validate_distribution_v7.py --plugin-root . --strict
```

Expected: all verifiable offline gates PASS; paid generation and publication remain separately NOT_RUN unless freshly authorized.

- [ ] **Step 5: Verify a fresh installed artifact**

Build/install using the repository's existing Marketplace workflow only after explicit installation authorization. From a fresh Codex task, verify discovery of `dreamina-video-shots`, `dreamina-video-sync`, `dreamina_reelbench_shots`, and `dreamina_reelbench_sync`; run read-only `status` and the no-paid local fixture. Record installed artifact SHA and cache path without private media.

- [ ] **Step 6: Stop for publication authorization**

Before pushing a release tag, Marketplace source update, or public release, present the exact local/remote SHA, CI status, package digest, installed evidence, remaining `NOT_RUN` gates, and proposed publication commands. Obtain a fresh publication approval ID. Do not reuse implementation approval.

- [ ] **Step 7: Commit evidence after authorized installation**

```bash
git add .codex-plugin/plugin.json .agents/plugins/marketplace.json README.md README.zh-CN.md docs/verification/command-coverage.md docs/verification/reelbench-local-acceptance.md tests/test_distribution.py tests/test_distribution_v7.py tests/test_video_project_skills.py tests/test_dreamina_mcp_server.py .github/workflows/ci.yml
git commit -m "release: prepare Dreamina Design 0.5.0"
```

Do not tag or publish in this step unless the fresh action-time publication authorization explicitly includes those actions.

---

## Final review gates

- [ ] Every task has a fresh implementer and independent specification/code review.
- [ ] `git diff --check`, full Python tests, both upstream self-tests, alias parity, distribution validation, and secret scan pass from an immutable commit archive.
- [ ] The repository, remote branch, CI commit, packaged artifact, installed artifact, and reported evidence all reference the same SHA.
- [ ] No ReelBench file differs from the pinned source beyond the two approved frontmatter names.
- [ ] No review MP4 is accepted as a final Dreamina project artifact.
- [ ] No paid or publication gate is marked PASS from authorization alone.
