# Dreamina MCP Full Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every official Dreamina CLI command category executable through typed, safe Codex MCP tools and publish the result as plugin version `0.3.0`.

**Architecture:** Keep `dreamina_mcp_server.py` as a thin schema/dispatch layer and put each command family in a focused service. Every service constructs fixed argv tokens for the existing trusted `DreaminaAdapter`; high-risk actions require native confirmation, paid actions retain approval/ledger guarantees, and read-only outputs pass through bounded secret redaction.

**Tech Stack:** Python 3.11+ standard library, dependency-free stdio MCP, `unittest`, JSON Schema Draft 2020-12, existing ApprovalGuard/OperationLedger/ReferencePolicy/ArtifactService.

**Spec:** `docs/superpowers/specs/2026-09-13-dreamina-mcp-full-automation-design.md`

## Global Constraints

- Preserve all existing `0.2.1` MCP request fields and tool names; additions are backward compatible.
- Target plugin version is exactly `0.3.0`.
- Never expose arbitrary shell strings, arbitrary argv, caller-provided executable paths, installer URLs, or installer digests.
- All schemas are closed with `additionalProperties: false`.
- Install/upgrade, relogin/logout, paid generation, and Session mutations require server-side native confirmation.
- No OAuth token, cookie, device code, authorization header, or unredacted log content may be persisted.
- Ambiguous paid submission outcomes are query-only and never automatically resubmitted.
- The 13 vendored upstream Skill trees remain byte-identical to `skills/.upstream-commit`.
- Tests use private synthetic CLIs; no automated test performs a real install, OAuth mutation, Session mutation, or paid generation.

## File responsibility map

- `scripts/dreamina_adapter.py`: trusted subprocess execution, bounded JSON/text results, no domain command construction.
- `scripts/output_redactor.py`: recursive structured/text credential redaction and response byte caps.
- `scripts/environment_service.py`: status, official installer download/execution, post-install trust enrollment.
- `scripts/auth_service.py`: login/headless/check/relogin/logout argv and secret-safe results.
- `scripts/account_service.py`: `user_credit` readiness projection.
- `scripts/task_service.py`: query/list/download orchestration without resubmission.
- `scripts/session_service.py`: Session CRUD argv, validation, and mutation approval.
- `scripts/diagnostic_service.py`: fixed-root log selection, bounded reads, and redaction.
- `scripts/image_service.py`: add `image_upscale` request and submission behavior.
- `scripts/video_service.py`: add `multiframe2video` request and transition behavior.
- `scripts/dreamina_mcp_server.py`: eleven tool definitions and thin handler delegation.
- `.mcp.json`: explicit per-tool approval modes.
- `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`: `0.3.0` metadata.
- `skills/dreamina-design-use/SKILL.md`: route users to the executable MCP automation tools.

---

### Task 1: Safe text execution and output redaction

**Files:**
- Create: `scripts/output_redactor.py`
- Create: `tests/test_output_redactor.py`
- Modify: `scripts/dreamina_adapter.py`
- Modify: `tests/test_dreamina_adapter.py`

**Interfaces:**
- Produces: `DreaminaTextResult(exit_code: int, stdout: str, stderr: str)`.
- Produces: `DreaminaAdapter.run_text(args: list[str]) -> DreaminaTextResult`.
- Produces: `redact_value(value: object) -> object` and `redact_text(text: str, *, max_bytes: int) -> str`.
- Consumers: environment, auth, account, task, Session, and diagnostic services.

- [x] **Step 1: Write failing adapter and redaction tests**

```python
def test_run_text_preserves_non_json_login_output(self):
    adapter = self._make_adapter("#!/bin/sh\necho 'verification_uri=https://example.test'\n")
    result = adapter.run_text(["login", "--headless"])
    self.assertEqual(result.exit_code, 0)
    self.assertIn("verification_uri", result.stdout)

def test_recursive_redaction_removes_sensitive_fields(self):
    value = {"user_id": "u1", "access_token": "secret", "nested": {"device_code": "d1"}}
    self.assertEqual(
        redact_value(value),
        {"user_id": "u1", "access_token": "[REDACTED]", "nested": {"device_code": "[REDACTED]"}},
    )
```

- [x] **Step 2: Run RED tests**

Run: `python3 -m unittest tests.test_output_redactor tests.test_dreamina_adapter -v`

Expected: fail because `DreaminaTextResult`, `run_text`, and redaction functions do not exist.

- [x] **Step 3: Implement the minimal bounded text seam and redactor**

```python
@dataclass(frozen=True)
class DreaminaTextResult:
    exit_code: int
    stdout: str
    stderr: str

def run_text(self, args: list[str]) -> DreaminaTextResult:
    binary = self._resolve_cli()
    exit_code, stdout, stderr = self._run_bounded_text([binary, *args])
    return DreaminaTextResult(exit_code=exit_code, stdout=stdout, stderr=stderr)
```

Implement recursive key matching for `token`, `cookie`, `authorization`,
`device_code`, `user_code`, and `secret`; redact bearer/cookie/token patterns in
text; UTF-8 truncate only after redaction.

- [x] **Step 4: Run GREEN tests and adapter regression**

Run: `python3 -m unittest tests.test_output_redactor tests.test_dreamina_adapter -v`

Expected: all tests pass with zero warnings.

- [x] **Step 5: Commit**

```bash
git add scripts/output_redactor.py scripts/dreamina_adapter.py tests/test_output_redactor.py tests/test_dreamina_adapter.py
git commit -m "feat: add bounded Dreamina text execution"
```

### Task 2: CLI status and account automation

**Files:**
- Create: `scripts/environment_service.py`
- Create: `scripts/account_service.py`
- Create: `tests/test_environment_service.py`
- Create: `tests/test_account_service.py`

**Interfaces:**
- Consumes: `DreaminaAdapter.run_text`, `DreaminaAdapter.capability_snapshot`, `redact_value`.
- Produces: `EnvironmentService.status(command: str | None, detail: str) -> dict[str, object]`.
- Produces: `AccountService.user_credit() -> dict[str, object]`.
- Later tasks extend `EnvironmentService` with installer execution.

- [x] **Step 1: Write failing status and account tests**

```python
def test_status_reports_trusted_version_and_selected_help(self):
    result = service.status(command="image_upscale", detail="summary")
    self.assertEqual(result["installed"], True)
    self.assertEqual(result["command"], "image_upscale")
    self.assertNotIn("access_token", json.dumps(result))

def test_account_returns_redacted_credit_payload(self):
    result = service.user_credit()
    self.assertEqual(result["credit_count"], 12)
    self.assertEqual(result["access_token"], "[REDACTED]")
```

- [x] **Step 2: Run RED tests**

Run: `python3 -m unittest tests.test_environment_service tests.test_account_service -v`

Expected: fail because both service modules are missing.

- [x] **Step 3: Implement fixed status/help and account argv**

Use only `--version`, `--help`, `<enum-command> --help`, and `user_credit`.
Accept the documented command enum only. Parse JSON when present, otherwise
return bounded redacted text with `installed`, `trusted`, `exit_code`, and
`requires_user_action` fields.

- [x] **Step 4: Run GREEN tests**

Run: `python3 -m unittest tests.test_environment_service tests.test_account_service tests.test_dreamina_adapter -v`

Expected: all tests pass.

- [x] **Step 5: Commit**

```bash
git add scripts/environment_service.py scripts/account_service.py tests/test_environment_service.py tests/test_account_service.py
git commit -m "feat: automate Dreamina status and account checks"
```

### Task 3: Authentication automation

**Files:**
- Create: `scripts/auth_service.py`
- Create: `tests/test_auth_service.py`

**Interfaces:**
- Consumes: `DreaminaAdapter.run_text`, `AccountService.user_credit`, `NativeApprovalProvider.confirm`, redaction functions.
- Produces: `AuthFlowStore` with ten-minute, single-use, memory-only flow records.
- Produces: `AuthService.execute(action: str, *, flow_id: str | None, poll_seconds: int) -> dict[str, object]`.

- [x] **Step 1: Write failing exact-argv and authorization tests**

```python
def test_headless_login_uses_fixed_argv_and_does_not_persist_device_code(self):
    result = service.execute("login_headless", flow_id=None, poll_seconds=0)
    self.assertEqual(adapter.calls, [["login", "--headless"]])
    self.assertEqual(result["requires_user_action"], True)
    self.assertIn("flow_id", result)
    self.assertNotIn("device_code", result)

def test_logout_denial_never_invokes_cli(self):
    provider.confirm.side_effect = ApprovalDeniedError("denied")
    with self.assertRaises(ApprovalDeniedError):
        service.execute("logout", flow_id=None, poll_seconds=0)
    self.assertEqual(adapter.calls, [])
```

- [x] **Step 2: Run RED test**

Run: `python3 -m unittest tests.test_auth_service -v`

Expected: fail because `AuthService` is missing.

- [x] **Step 3: Implement closed action dispatch**

Map actions exactly to fixed argv. `login_headless` extracts the CLI device code
into `AuthFlowStore` and returns a random opaque flow ID; `check_login` consumes
that flow ID to build the CLI argv:

```python
{
    "login": ["login"],
    "login_headless": ["login", "--headless"],
    "check_login": ["login", "checklogin", "--device_code", flow_store.consume(flow_id), "--poll", str(poll_seconds)],
    "relogin": ["relogin"],
    "logout": ["logout"],
}
```

Bound polling to 0–300 seconds. Flow IDs expire after 600 seconds, are consumed
once, live only in the MCP process, and are not restored after restart. Require
native confirmation for `relogin` and `logout`. Verify successful
login/relogin/check with `user_credit`. Redact sensitive fields before returning
and never write them to disk.

- [x] **Step 4: Run GREEN and denial/timeout tests**

Run: `python3 -m unittest tests.test_auth_service tests.test_account_service -v`

Expected: success, denial, malformed output, and timeout tests all pass.

- [x] **Step 5: Commit**

```bash
git add scripts/auth_service.py tests/test_auth_service.py
git commit -m "feat: add Dreamina authentication automation"
```

### Task 4: Session CRUD automation

**Files:**
- Create: `scripts/session_service.py`
- Create: `tests/test_session_service.py`

**Interfaces:**
- Consumes: `DreaminaAdapter.run_text`, `NativeApprovalProvider.confirm`, redaction functions.
- Produces: `SessionService.execute(action: str, *, name: str | None, session_id: str | None, query: str | None) -> dict[str, object]`.

- [x] **Step 1: Write failing CRUD routing and default-delete tests**

```python
def test_rename_uses_exact_arguments_after_confirmation(self):
    service.execute("rename", session_id="123", name="new name", query=None)
    self.assertEqual(adapter.calls, [["session", "rename", "123", "new name"]])

def test_default_session_delete_is_rejected_before_approval(self):
    with self.assertRaises(ValueError):
        service.execute("delete", session_id="0", name=None, query=None)
    self.assertEqual(adapter.calls, [])
```

- [x] **Step 2: Run RED test**

Run: `python3 -m unittest tests.test_session_service -v`

Expected: fail because `SessionService` is missing.

- [x] **Step 3: Implement closed CRUD dispatch and validation**

Allow only create/list/search/rename/delete. Require names and queries to be
1–200 UTF-8 characters, Session IDs to match `^[A-Za-z0-9_-]{1,128}$`, and
native confirmation for create/rename/delete. Reject Session `0` deletion.

- [x] **Step 4: Run GREEN tests**

Run: `python3 -m unittest tests.test_session_service -v`

Expected: exact argv, validation, approval, denial, and redaction tests pass.

- [x] **Step 5: Commit**

```bash
git add scripts/session_service.py tests/test_session_service.py
git commit -m "feat: automate Dreamina session management"
```

### Task 5: Task query, list, and verified download automation

**Files:**
- Create: `scripts/task_service.py`
- Create: `tests/test_task_service.py`
- Modify: `scripts/artifact_service.py`
- Modify: `tests/test_artifact_service.py`

**Interfaces:**
- Consumes: `DreaminaAdapter.run`, `OperationLedger`, `ArtifactService`.
- Produces: `TaskService.query(submit_id: str, poll_seconds: int, download_dir: Path | None, approved_roots: list[Path]) -> dict[str, object]`.
- Produces: `TaskService.list_tasks(filters: Mapping[str, object], limit: int) -> dict[str, object]`.
- Produces: external-query provenance receipts distinct from plugin-submitted receipts.

- [x] **Step 1: Write failing query/list/download tests**

```python
def test_unknown_submit_id_queries_once_without_resubmission(self):
    result = service.query("external-1", poll_seconds=0, download_dir=None, approved_roots=[])
    self.assertEqual(adapter.calls, [["query_result", "--submit_id", "external-1"]])
    self.assertEqual(result["provenance"], "externally-queried")

def test_list_tasks_applies_closed_filters_and_limit(self):
    service.list_tasks({"gen_status": "success"}, limit=20)
    self.assertEqual(adapter.calls, [["list_task", "--gen_status", "success", "--limit", "20"]])
```

- [x] **Step 2: Run RED tests**

Run: `python3 -m unittest tests.test_task_service tests.test_artifact_service -v`

Expected: fail because `TaskService` and external-query provenance are missing.

- [x] **Step 3: Implement query-only polling, bounded lists, and downloads**

Validate submit IDs, bound polling to 0–300 seconds, and limit results to
1–100. Build only documented list filters confirmed by captured CLI help.
Downloads require an approved root and reuse no-overwrite/symlink/MIME/size
checks. Never call a generation command from this service.

- [x] **Step 4: Run GREEN and regression tests**

Run: `python3 -m unittest tests.test_task_service tests.test_artifact_service tests.test_operation_ledger -v`

Expected: all query, provenance, recovery, and artifact tests pass.

- [x] **Step 5: Commit**

```bash
git add scripts/task_service.py scripts/artifact_service.py tests/test_task_service.py tests/test_artifact_service.py
git commit -m "feat: automate Dreamina task recovery and downloads"
```

### Task 6: Image upscale automation

**Files:**
- Modify: `scripts/image_service.py`
- Modify: `tests/test_image_service.py`

**Interfaces:**
- Consumes: existing capability snapshot, reference staging, approval guard, adapter, operation ledger.
- Produces: `ImageService.build_request(mode="image_upscale", ...)` with an exact one-reference contract.

- [x] **Step 1: Write failing upscale request and argv tests**

```python
def test_image_upscale_rejects_prompt_model_ratio_and_count(self):
    with self.assertRaises(InvalidRequestError):
        service.build_request(mode="image_upscale", prompt="x", model="5.0Pro", resolution_type="4k", count=2, ratio="1:1", references=[reference])

def test_image_upscale_submits_exact_fixed_argv(self):
    service.submit(request, adapter=adapter, approval_guard=guard, session_id=session_id, approval_id=approval_id)
    self.assertEqual(adapter.calls[0][:3], ["image_upscale", "--image", request["references"][0]["staged_path"]])
```

- [x] **Step 2: Run RED test**

Run: `python3 -m unittest tests.test_image_service -v`

Expected: fail because `image_upscale` is unsupported.

- [x] **Step 3: Implement mode-specific request and fingerprint**

Require exactly one staged image and `resolution_type`; forbid unrelated fields;
append only `image_upscale --image <path> --resolution_type <value> --poll 0`.
Bind reference digest, resolution, and destination to approval.

- [x] **Step 4: Run GREEN tests**

Run: `python3 -m unittest tests.test_image_service tests.test_reference_policy tests.test_approval_guard -v`

Expected: all image and safety tests pass.

- [x] **Step 5: Commit**

```bash
git add scripts/image_service.py tests/test_image_service.py
git commit -m "feat: automate Dreamina image upscaling"
```

### Task 7: Multi-frame video automation

**Files:**
- Modify: `scripts/dreamina_adapter.py`
- Modify: `scripts/video_service.py`
- Modify: `tests/test_dreamina_adapter.py`
- Modify: `tests/test_video_service.py`

**Interfaces:**
- Consumes: live `multiframe2video --help`, ordered staged references, approval guard, operation ledger.
- Produces: capability entry and request/argv support for `multiframe2video`.

- [x] **Step 1: Write failing capability, validation, fingerprint, and argv tests**

```python
def test_multiframe_requires_two_to_twenty_ordered_images(self):
    with self.assertRaises(InvalidReferenceError):
        service.build_request(mode="multiframe2video", prompt="story", model=None, video_resolution="720p", ratio=None, duration_seconds=3, references=[one_image])

def test_multiframe_three_images_require_zero_or_two_transitions(self):
    with self.assertRaises(InvalidRequestError):
        service.build_request(mode="multiframe2video", prompt="story", model=None, video_resolution="720p", ratio=None, duration_seconds=3, references=three_images, transitions=[{"prompt": "only one", "duration_seconds": 3}])
```

- [x] **Step 2: Run RED tests**

Run: `python3 -m unittest tests.test_video_service tests.test_dreamina_adapter -v`

Expected: fail because the mode and transitions are unsupported.

- [x] **Step 3: Implement runtime-discovered multi-frame support**

Add `multiframe2video` to capability discovery. Accept no model or ratio unless
live help advertises them. Build ordered `--images` plus the exact repeated
transition flags observed in help. Bind reference order and transitions into the
request fingerprint and approval scope.

- [x] **Step 4: Run GREEN tests**

Run: `python3 -m unittest tests.test_video_service tests.test_dreamina_adapter tests.test_reference_policy -v`

Expected: mode constraints, exact argv, approval mismatch, and no-resubmit tests pass.

- [x] **Step 5: Commit**

```bash
git add scripts/dreamina_adapter.py scripts/video_service.py tests/test_dreamina_adapter.py tests/test_video_service.py
git commit -m "feat: automate Dreamina multi-frame video"
```

### Task 8: Redacted log diagnosis

**Files:**
- Create: `scripts/diagnostic_service.py`
- Create: `tests/test_diagnostic_service.py`

**Interfaces:**
- Consumes: fixed `~/.dreamina_cli/logs/` root, `EnvironmentService.status`, redaction functions.
- Produces: `DiagnosticService.diagnose(command: str, error: str, submit_id: str | None, since_minutes: int, max_files: int) -> dict[str, object]`.

- [x] **Step 1: Write failing fixed-root, symlink, redaction, and cap tests**

```python
def test_diagnose_reads_only_recent_regular_logs_and_redacts_tokens(self):
    result = service.diagnose("dreamina text2image", "failed", None, 30, 3)
    body = json.dumps(result)
    self.assertIn("dreamina text2image", body)
    self.assertNotIn("Bearer secret", body)

def test_symlink_log_is_rejected(self):
    symlink.symlink_to(outside_file)
    result = service.diagnose("dreamina version", "failed", None, 30, 3)
    self.assertNotIn(outside_file.read_text(), json.dumps(result))
```

- [x] **Step 2: Run RED test**

Run: `python3 -m unittest tests.test_diagnostic_service -v`

Expected: fail because `DiagnosticService` is missing.

- [x] **Step 3: Implement bounded `O_NOFOLLOW` log reads**

Accept no path input. Bound `since_minutes` to 1–1440, `max_files` to 1–10,
per-file reads to 64 KiB, and total redacted output to 256 KiB. Sort by mtime,
open regular files with `O_NOFOLLOW`, verify inode metadata after open, redact,
then return exact command/error/version/submit context.

- [x] **Step 4: Run GREEN tests**

Run: `python3 -m unittest tests.test_diagnostic_service tests.test_output_redactor -v`

Expected: traversal, symlink, replacement race, secret, and byte-cap tests pass.

- [x] **Step 5: Commit**

```bash
git add scripts/diagnostic_service.py tests/test_diagnostic_service.py
git commit -m "feat: add redacted Dreamina diagnostics"
```

### Task 9: Verified installer and upgrade automation

**Files:**
- Modify: `scripts/environment_service.py`
- Modify: `tests/test_environment_service.py`
- Modify: `scripts/trusted_cli.py`
- Modify: `tests/test_trusted_cli.py`

**Interfaces:**
- Consumes: `NativeApprovalProvider.confirm`, HTTPS client from Python standard library, `TrustedCliStore.enroll`.
- Produces: `EnvironmentService.install_or_upgrade(action: str) -> dict[str, object]`.

- [x] **Step 1: Write failing download-policy, denial, execution, and enrollment tests**

```python
def test_installer_rejects_redirect_to_unapproved_host(self):
    downloader.final_url = "https://evil.example/install.sh"
    with self.assertRaises(InstallerTrustError):
        service.install_or_upgrade("install")

def test_denial_does_not_execute_downloaded_installer(self):
    provider.confirm.side_effect = ApprovalDeniedError("denied")
    with self.assertRaises(ApprovalDeniedError):
        service.install_or_upgrade("upgrade")
    self.assertEqual(runner.calls, [])
```

- [x] **Step 2: Run RED test**

Run: `python3 -m unittest tests.test_environment_service tests.test_trusted_cli -v`

Expected: fail because installer execution is missing.

- [x] **Step 3: Implement fixed HTTPS installer workflow**

Use only `https://jimeng.jianying.com/cli`. Limit redirects to HTTPS and the
approved host set, cap body size at 2 MiB, save with mode `0500` under a `0700`
temporary directory, calculate SHA-256, and present action/URL/final URL/digest
to native approval. Execute via `["/bin/bash", installer_path]` with the adapter's
minimal environment and bounded output. On success resolve the installed CLI,
invoke native trust enrollment, then verify version and help. Never mark trust
complete before both probes succeed.

- [x] **Step 4: Run GREEN and security regression tests**

Run: `python3 -m unittest tests.test_environment_service tests.test_trusted_cli tests.test_dreamina_adapter -v`

Expected: host/scheme/redirect/oversize/denial/failure/success cases pass.

- [x] **Step 5: Commit**

```bash
git add scripts/environment_service.py scripts/trusted_cli.py tests/test_environment_service.py tests/test_trusted_cli.py
git commit -m "feat: add verified Dreamina CLI installation"
```

### Task 10: Wire eleven MCP tools and approval policy

**Files:**
- Modify: `scripts/dreamina_mcp_server.py`
- Modify: `tests/test_dreamina_mcp_server.py`
- Modify: `.mcp.json`
- Modify: `tests/test_distribution.py`
- Modify: `tests/test_contracts.py`
- Modify: `skills/dreamina-design-use/SKILL.md`
- Modify: `tests/test_router_skill.py`

**Interfaces:**
- Consumes: all services from Tasks 2–9.
- Produces: eleven public tools specified in the design, all returning structured MCP results.

- [x] **Step 1: Write failing MCP inventory, schema, annotation, and dispatch tests**

```python
EXPECTED_TOOLS = {
    "dreamina_capability_snapshot", "dreamina_cli_status",
    "dreamina_cli_install_or_upgrade", "dreamina_auth", "dreamina_account",
    "dreamina_submit_image", "dreamina_submit_video", "dreamina_query_task",
    "dreamina_list_tasks", "dreamina_session", "dreamina_diagnose",
}

def test_full_automation_tool_inventory(self):
    self.assertEqual({tool["name"] for tool in _tool_definitions()}, EXPECTED_TOOLS)
    self.assertTrue(all(tool["inputSchema"]["additionalProperties"] is False for tool in _tool_definitions()))
```

Add handler tests asserting each tool delegates once to its service, rejects
unknown properties, returns `structuredContent`, and translates errors into the
specified structured error shape.

- [x] **Step 2: Run RED tests**

Run: `python3 -m unittest tests.test_dreamina_mcp_server tests.test_distribution tests.test_contracts tests.test_router_skill -v`

Expected: fail because eight tools and new policy entries are absent.

- [x] **Step 3: Implement schemas and thin dispatch**

Keep tool construction in focused helper functions. Add explicit `.mcp.json`
entries: read-only status/capability/account/query/list/diagnose use `approve`;
install/auth/session and both paid submit tools use `prompt`. Mark filesystem
download and interactive login annotations accurately instead of treating all
actions within a multi-action tool as read-only.

Update the router Skill to prefer executable MCP tools and identify the exact
actions that pause for native/user approval.

- [x] **Step 4: Run GREEN tests and protocol smoke test**

Run: `python3 -m unittest tests.test_dreamina_mcp_server tests.test_distribution tests.test_contracts tests.test_router_skill -v`

Run: `printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python3 -m scripts.dreamina_mcp_server`

Expected: tests pass; `tools/list` returns exactly eleven tools.

- [x] **Step 5: Commit**

```bash
git add scripts/dreamina_mcp_server.py tests/test_dreamina_mcp_server.py .mcp.json tests/test_distribution.py tests/test_contracts.py skills/dreamina-design-use/SKILL.md tests/test_router_skill.py
git commit -m "feat: expose complete Dreamina MCP automation"
```

### Task 11: Version, documentation, full validation, and publication

**Files:**
- Modify: `.codex-plugin/plugin.json`
- Modify: `.agents/plugins/marketplace.json`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/Codex-Dreamina-Design-Plugin-Architecture.md`
- Modify: `docs/Codex-Dreamina-Design-Plugin-Architecture.zh_CN.md`
- Modify: `docs/superpowers/plans/2026-09-13-dreamina-mcp-full-automation.md`
- Create: `docs/verification/mcp-full-automation-2026-09-13.md`
- Modify: distribution and version assertions under `tests/`.

**Interfaces:**
- Consumes: all completed automation tools and validators.
- Produces: released `0.3.0` plugin, public installation evidence, and completed plan ledger.

- [x] **Step 1: Write failing version and documentation assertions**

```python
def test_release_version_is_030(self):
    self.assertEqual(plugin_manifest["version"], "0.3.0")
    self.assertEqual(marketplace_plugin["version"], "0.3.0")
```

Add an automated table assertion mapping every original coverage row to a
documented MCP tool and tested handler.

- [x] **Step 2: Run RED release tests**

Run: `python3 -m unittest tests.test_distribution tests.test_official_cli_skill_coverage -v`

Expected: fail because manifests still report `0.2.1` and the executable
coverage evidence is incomplete.

- [x] **Step 3: Update version, docs, and evidence**

Set manifests to `0.3.0`. Document all eleven tools, approval pauses, automation
examples, recovery semantics, and the distinction between help-only probes and
mutating runtime validation. Check plan boxes only as their commands pass.

- [x] **Step 4: Run complete local verification**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_distribution.py
python3 scripts/validate_distribution_v7.py --strict --plan-gate --require-runtime-gates
python3 scripts/verify_skill_snapshot.py --upstream-root /Users/wandl/workspaces/workspace-agent-skills/full-aigc-skills-repositories/dreamina-skills
python3 scripts/run_strict_trace.py
python3 /Users/wandl/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
git diff --check
```

Expected: all commands exit zero; 13 upstream Skill trees match the pinned
commit and 14 plugin Skills pass TRACE.

- [x] **Step 5: Perform allowed real read-only acceptance**

Call status/version/help, account, task list, Session list/search, and bounded
diagnosis through the MCP server. Record commands and redacted results. Do not
install/upgrade, alter OAuth state, mutate Sessions, or submit paid tasks without
separate action-time approval.

- [x] **Step 6: Run security review and resolve findings**

Review installer trust, native confirmation binding, secret redaction, log file
races, path policies, exact argv, output caps, and paid no-resubmit behavior.
Repeat affected tests after each accepted fix. Release requires zero Critical,
High, or production-blocking Medium findings.

- [x] **Step 7: Commit and push release**

```bash
git add .codex-plugin/plugin.json .agents/plugins/marketplace.json README.md README.zh-CN.md docs tests scripts skills/dreamina-design-use/SKILL.md .mcp.json
git commit -m "release: publish Dreamina automation 0.3.0"
git push origin main
```

- [x] **Step 8: Wait for terminal GitHub CI success**

Run: `gh run list --repo partme-ai/codex-dreamina-design-plugin --commit "$(git rev-parse HEAD)" --limit 1`

Then wait on the returned run with `gh run watch <run-id> --repo partme-ai/codex-dreamina-design-plugin --exit-status`.

Expected: terminal conclusion `success`.

- [x] **Step 9: Refresh and reinstall the public plugin**

```bash
codex plugin marketplace upgrade partme-ai-dreamina-design
codex plugin remove codex-dreamina-design@partme-ai-dreamina-design
codex plugin add codex-dreamina-design@partme-ai-dreamina-design
```

Verify the installed cache reports version `0.3.0`, contains all eleven tool
definitions, and byte-matches the released router and upstream Skill trees.

- [x] **Step 10: Verify final repository identity**

Run:

```bash
git rev-parse HEAD
git rev-parse origin/main
git ls-remote origin refs/heads/main
git status --short --branch
```

Expected: local, tracking, and remote SHAs are equal and the plugin worktree is clean.
