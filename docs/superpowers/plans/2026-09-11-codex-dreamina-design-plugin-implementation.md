# Codex Dreamina Design Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build current, safe Dreamina image/video workflows for Codex.

**Architecture:** Migrated Dreamina Skills call a strict CLI adapter backed by capability, approval, operation, and artifact contracts.

**Tech Stack:** Codex plugin, Agent Skills, Python, JSON Schema, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-09-11-codex-dreamina-design-plugin-design.md`

## Constraints

- ID `codex-dreamina-design`; no `jimeng-*` installable identities.
- Runtime CLI contract is authoritative; no paid CI calls.
- Approval is request-bound; async uncertainty never causes resubmission.

### Task 1: Migrate dreamina-skills
- [ ] Write a failing inventory test covering all 13 old directories, frontmatter names, links, README and install commands.
- [ ] Rename the GitHub/local repository and each `jimeng-*` directory through an explicit mapping ledger.
- [ ] Update references and current CLI contract snapshots; run 13 quick validations and TRACE.
- [ ] Prove zero installable `jimeng-*` identities; commit and publish the migration.

### Task 2: Plugin contracts and adapter
- [ ] Write failing manifest and closed-schema tests.
- [ ] Add capability/generation/approval/operation/artifact schemas and argv-only CLI adapter.
- [ ] Test errors and read-only discovery; commit `feat: define Dreamina Design runtime`.

### Task 3: Image workflows
- [ ] Baseline failures for unsupported model/resolution, reference scope, batch count and blind retry.
- [ ] Package and validate text-to-image and image-to-image Skills.
- [ ] Run offline fixtures; commit `feat: add Dreamina image workflows`.

### Task 4: Video workflows
- [ ] Write failing tests for text/image/frame/multimodal modes, web prerequisite, ratio, duration and resolution discovery.
- [ ] Package and validate video Skills using live capability snapshots.
- [ ] Run fixtures; commit `feat: add Dreamina video workflows`.

### Task 5: Async recovery and downloads
- [ ] Write failing state-machine tests including restart and unknown submission outcomes.
- [ ] Implement atomic non-secret ledger, bounded polling and artifact verification.
- [ ] Prove no timeout resubmission; commit `feat: recover Dreamina design tasks`.

### Task 6: Distribution and acceptance
- [ ] Add repository marketplace and failing distribution tests.
- [ ] Run quick validation, TRACE, links, secret scans and plugin validation.
- [ ] Run only read-only CLI runtime checks; keep credit canary separately approved.
- [ ] Commit `test: verify Dreamina Design distribution`.
