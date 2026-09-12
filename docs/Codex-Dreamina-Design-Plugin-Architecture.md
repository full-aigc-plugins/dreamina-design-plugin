# Codex Dreamina Design Plugin Architecture

> Feature architecture implemented; production hardening in progress. Re-verified 2026-09-13.

## Context

```mermaid
flowchart LR
    User --> Router[Intent Router]
    Router --> Prompt[Prompt Skills]
    Router --> Capability[CLI Capability Snapshot]
    Prompt --> Request[Generation Request]
    Capability --> Request
    UI[Codex MCP tool approval prompt] --> Approval[Single-use Approval Guard]
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
Paid actions are exposed only through MCP tools configured with
`approval_mode: prompt`; caller-supplied identity assertions are not accepted.
