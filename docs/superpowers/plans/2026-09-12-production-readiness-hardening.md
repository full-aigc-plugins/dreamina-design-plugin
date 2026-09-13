# Codex Dreamina Design Production Readiness Hardening

**Goal:** Make the plugin self-contained, safe for paid actions, resilient to
process failure/concurrency, and verifiable from a fresh Codex installation.

**Source:** Extends the completed 2026-09-11 implementation plan. This file is
the execution ledger for production readiness; the older plan remains the
historical feature baseline.

## P1 — Paid-action authorization and local state safety

- [x] Reject session path traversal and symlink escape.
- [x] Replace caller-forgeable approval dictionaries with guard-issued opaque IDs.
- [x] Validate approval scope against the exact request, expire approvals, and consume them once.
- [x] Add cross-process locks and fsync-backed atomic writes for approval/operation state.
- [x] Add concurrent-process stress tests proving no duplicate session creation or approval consumption.
- [x] Route paid submissions through MCP tools plus a server-side native dialog; enroll the CLI path/hash through a separate native trust dialog and protected `0700/0600` configuration.

## P2 — CLI runtime and recovery

- [x] Bound stdout/stderr while the process is running and kill the process group on overflow/timeout.
- [x] Require an absolute non-symlink CLI path, trusted permissions/owner, pinned SHA-256, and a minimal environment.
- [x] Use the observed `query_result --submit_id` contract and map `gen_status` terminal states.
- [x] Record every accepted `submit_id` in the operation ledger.
- [x] Persist a pre-invocation `SUBMITTING` intent and force unknown transport/crash outcomes into durable manual review; do not claim provider exactly-once without an idempotency key.
- [x] Add bounded polling with restart/resume and unknown-submit manual-reconciliation tests.
- [x] Validate the existing-task query → download path against the installed CLI without creating a new paid task; paid submit remains a separate canary.

## P3 — Reference and artifact trust boundaries

- [x] Require HTTPS and approved artifact hosts.
- [x] Restrict destinations to an approved root, reject overwrite/symlinks, and cap payload size.
- [x] Enforce approved roots, regular-file/no-symlink checks, MIME sniffing, and size limits for every uploaded reference.
- [x] Use the CLI `query_result --download_dir` path for production downloads and verify the resulting local file inside an approved root.
- [x] Bind artifact provenance to a recorded `succeeded/download` operation and verify returned MIME/dimensions before issuing the receipt.

## P4 — Packaged Skill supply chain

- [x] Replace metadata stubs with complete packaged Skill trees.
- [x] Pin the reviewed upstream commit in `skills/.upstream-commit`.
- [x] Byte-compare every packaged file with the pinned upstream checkout.
- [x] Run local and upstream strict TRACE.
- [x] Keep plugin-owned files whitespace-clean while preserving byte-identical upstream whitespace inside vendored Skill trees.
- [x] Advance the pin from `373bf7f` to upstream `300bfc1` after proving the 13 Design Skill trees are unchanged; the intervening commits add Canvas Skills only.

## P5 — Distribution and production acceptance

- [x] Run the official plugin validator in an isolated environment with pinned PyYAML 6.0.3.
- [x] Install the local candidate through a disposable marketplace and prove all 14 Skills are discovered and cached with executable bodies.
- [x] Complete final security re-review after native approve/deny paths, trusted CLI enrollment, and canary; zero Critical/High/direct production-blocking Medium findings remain.
- [x] Run one separately approved minimum-specification canary and preserve submit/query/download/artifact/cost evidence.
- [x] Publish version 0.2.0, reinstall from the public marketplace, verify 14 complete Skills and no-argument trusted MCP runtime, and prove local/tracking/remote SHA equality.

## Production completion gate

Production readiness was achieved on 2026-09-13 after every checkbox above was
completed with offline, runtime, security, canary, publication, and fresh public
installation evidence. The user subsequently confirmed that functional
verification was complete and passed; the acceptance record is preserved in
`docs/verification/user-functional-acceptance-2026-09-13.md`.
