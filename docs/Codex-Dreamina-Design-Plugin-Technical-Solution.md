# Codex Dreamina Design Plugin Technical Solution

> **Document control**
>
> | Field | Value |
> |---|---|
> | Status | Implemented as `0.4.0`; reference-video runtime gates recorded `NOT_RUN` |
> | Scope | How the plugin wraps the CLI, what the contracts are, and how they are verified |
> | Audience | Implementers extending or reviewing this plugin |
> | Runtime evidence | `docs/verification/` |

## 1. Decision

Build a skills-first compatibility plugin around the installed `dreamina` CLI. Use a shared subprocess adapter, capability snapshot, approval guard, operation ledger, and artifact validator.

### Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Call the remote API directly | Would duplicate authentication, entitlement, and pricing that the CLI already owns |
| Expose the CLI through a generic "run command" tool | Makes arbitrary execution reachable from a model-authored argument |
| Keep the previous `jimeng-*` Skill identities | The canonical upstream names are `dreamina-*`, and two identities would drift |
| Treat approval as an MCP flag only | A flag is metadata; the paid action needs a fail-closed human confirmation |
| Auto-retry a long generation | The call is paid; a retry can duplicate a charge with no proof of which attempt succeeded |

## 2. Implemented layout

```text
.codex-plugin/plugin.json
.mcp.json
scripts/dreamina_mcp_server.py
scripts/video_project_mcp.py
scripts/dreamina_adapter.py
scripts/approval_guard.py
scripts/operation_ledger.py
scripts/trusted_cli.py
scripts/native_approval.py
scripts/video_project_store.py
scripts/reference_policy.py
skills/
tests/
```

| Path | Responsibility |
|---|---|
| `scripts/dreamina_mcp_server.py` | stdio JSON-RPC dispatch and the error envelope |
| `scripts/video_project_mcp.py` | The ten reference-video project tools |
| `scripts/dreamina_adapter.py` | argv-only CLI invocation with typed failures |
| `scripts/image_service.py`, `scripts/video_service.py`, `scripts/task_service.py` | Request construction, submission, and query |
| `scripts/auth_service.py`, `scripts/session_service.py` | Closed OAuth and Session command sets |
| `scripts/approval_guard.py`, `scripts/native_approval.py` | Single-use approval persistence and the fail-closed dialog |
| `scripts/trusted_cli.py` | CLI trust enrolment and protected storage |
| `scripts/video_project_store.py` | Versioned project state with compare-and-swap transitions |
| `scripts/reference_policy.py` | Containment, type, and size policy for local inputs |

## 3. Migration map

The 13 existing Skill directories are renamed mechanically from `jimeng-*` to `dreamina-*`; `dreamina-cli` remains canonical. Directory name, frontmatter name, links, examples, READMEs, GitHub paths, and install commands change together. A validator rejects any remaining `jimeng-` identity while allowing product copy that explains the former name.

Upstream remains the fact source: `dreamina-skills` is pinned by commit in `skills/.upstream-commit`, and packaged Skill trees are verified byte for byte.

## 4. Contracts

- `CapabilitySnapshot`: CLI version/commit plus current schema, or a command-help snapshot when the installed CLI has no `schema` command.
- `GenerationRequest`: mode, prompt, references, model token, resolution, ratio, duration, count.
- `ApprovalReceipt`: exact request fingerprint and quote/credit acknowledgement.
- `OperationReceipt`: session, submit ID, state, timestamps, required action.
- `ArtifactReceipt`: local path, checksum, media metadata, source submit ID.

| Contract | Invariant |
|---|---|
| `CapabilitySnapshot` | Read live; never treated as authoritative after the turn ends |
| `GenerationRequest` | Rejected on an unknown field or an unsupported parameter |
| `ApprovalReceipt` | Single use, five-minute lifetime, bound to the request fingerprint |
| `OperationReceipt` | Keyed by `submit_id`; contains no credential-like field |
| `ArtifactReceipt` | A checksum mismatch is a failure, not a warning |

## 5. Configuration and state

| Setting | Location | Notes |
|---|---|---|
| MCP server | `.mcp.json` | stdio; startup timeout 10 seconds, tool timeout 3600 seconds |
| Tool approval mode | `.mcp.json` | `approve` for read-only tools, `prompt` for paid and mutating tools |
| Trust record | `~/.config/dreamina-design/trusted-cli.json` | File `0600`, directory `0700`; path and digest |
| State root | `~/.local/share/dreamina-design/` | Holds `operations/` and `approvals/` |
| Reference policy | `scripts/reference_policy.py` | 50 MiB per image, 512 MiB per media file, containment and type checks |

## 6. Error model

Failures return a structured envelope with `error_type`, `message`, `retryable`, `requires_user_action`, and `next_action`. `next_action` is one of `request_user_action`, `query_same_submit_id`, or `correct_request`.

| Condition | Envelope behavior |
|---|---|
| Login required or session expired | `requires_user_action` true; `next_action` is `request_user_action` |
| Permission or entitlement denied | `requires_user_action` true |
| Ambiguous submission | Reservation consumed; the operation enters manual review |
| Retryable query failure | `retryable` true, only for the query tool |
| Method not found | JSON-RPC `-32601` |

## 7. Testing

Use versioned help/schema snapshots from an installed CLI without generation. Synthetic fixtures cover auth, permission, upgrade, validation, querying, failure, cancellation and downloads. A credit-consuming canary is never part of ordinary CI.

| Layer | Proves | Command |
|---|---|---|
| Unit and contract | Services, guards, ledger, trust enrolment, reference policy | `python3 -m unittest discover -s tests` |
| Distribution | Required files, manifest references, secret scan | `python3 scripts/validate_distribution.py .` |
| Runtime gates | Plan gate and runtime gate lines | `python3 scripts/validate_distribution_v7.py --plan-gate`, then `--require-runtime-gates` |
| Skill parity | Byte-identical packaged Skills | `python3 scripts/verify_skill_snapshot.py --strict` |
| TRACE | Per-skill behavioural contract | `python3 scripts/run_strict_trace.py` |
| Paid canary | A real credit-consuming call | Separate authorized gate, recorded under `docs/verification/` |

## 8. Compatibility and evidence map

| Aspect | Position |
|---|---|
| Python | 3.13 |
| CLI | Installed from the official installer and enrolled through native trust |
| Reference-video tools | Implemented; all runtime gate lines `NOT_RUN` |
| Paid canary | Separately approved or `NOT_RUN` |
| Rollback | Revert the plugin; receipts remain readable because the format is additive |

| Claim | Evidence |
|---|---|
| Tool catalogue and approval modes | `.mcp.json` |
| Approval enforcement | `scripts/approval_guard.py`, `scripts/native_approval.py` |
| Reference containment | `scripts/reference_policy.py` |
| Runtime gate lines | `docs/verification/reference-video-runtime-2026-09-14.md` |
| Skill parity | `scripts/verify_skill_snapshot.py` |
