# Codex Dreamina Design Plugin Architecture

## 0.3.0 full automation layer

The stdio MCP server exposes eleven closed-schema tools spanning CLI status,
verified install/update, memory-only OAuth flows, account readiness, all eight
generation modes, task query/list/download, Session CRUD, and redacted log
diagnosis. Domain services construct fixed argv; the MCP server does not expose
arbitrary shell or command execution. High-risk operations are enforced by a
server-side native confirmation in addition to MCP approval metadata.

```mermaid
flowchart LR
    Codex --> MCP[11 typed MCP tools]
    MCP --> Services[Domain services]
    Services --> Guard[Native approval and request guard]
    Services --> Adapter[Trusted argv-only adapter]
    Adapter --> CLI[Dreamina CLI]
    Services --> Ledger[Operation ledger]
    Services --> Verify[Artifact and log verification]
```

> Feature architecture implemented; production hardening in progress. Re-verified 2026-09-13.

## Context

```mermaid
flowchart LR
    User --> Router[Intent Router]
    Router --> Prompt[Prompt Skills]
    Router --> Capability[CLI Capability Snapshot]
    Prompt --> Request[Generation Request]
    Capability --> Request
    UI[Codex MCP prompt + native dialog] --> Approval[Single-use Approval Guard]
    Request --> Approval
    Approval --> CLI[dreamina CLI]
    Approval --> Intent[Durable SUBMITTING intent]
    Intent --> CLI[Trusted absolute CLI + SHA-256]
    CLI --> Ledger[submit_id Ledger]
    Ledger --> Query[Bounded Query]
    Query --> Download[CLI download into approved root]
    Download --> Artifact[Validated Artifact]
```

## Bounded contexts

| Context | Responsibility |
|---|---|
| Prompt | expressive content without inventing unsupported parameters |
| Capability | live models, resolutions, ratios, duration and flags |
| Generation | normalized image/video request and request fingerprint |
| Approval | exact cost/scope confirmation before submission |
| Operation | submit ID, terminal state, recovery and history |
| Artifact | downloads, checksums, media metadata and provenance |

## Runtime rules

The CLI is the remote authority. The plugin does not implement private Dreamina APIs. Each generation is submitted once; timeouts enter `Unknown` and reconcile through query/history. The result is not complete until artifact verification succeeds.

## Skill topology

`dreamina-skills` remains the reusable Skill fact source. This plugin packages
complete byte-verified Skill trees pinned by `skills/.upstream-commit`.

## Security

No credentials in manifests, logs, prompts, ledgers, or artifacts. Local
reference files require approved-root, regular-file, type and size validation.
Paid actions are exposed through MCP tools configured with `approval_mode:
prompt` and independently require a server-side native dialog. CLI identity is
loaded only from a separately enrolled private trust configuration.
