# Codex Dreamina Design Plugin

<img src="assets/logo.png" alt="Dreamina Design logo" width="128">

> Compatibility foundation for Dreamina CLI image and video workflows.

[English](README.md) | [简体中文](README.zh-CN.md)

## Status and purpose

**Feature baseline complete; production hardening is in progress.**

The offline implementation gate is satisfied, but production acceptance is
tracked separately in
[`docs/superpowers/plans/2026-09-12-production-readiness-hardening.md`](docs/superpowers/plans/2026-09-12-production-readiness-hardening.md).
Paid canary, fresh local/public installation, input-file containment, final
security review, and remote publication are not yet complete.

```text
Creative intent -> prompt contract -> live CLI capability discovery
                -> explicit generation approval -> submit once
                -> query by submit_id -> validate/download artifacts
```

All seven baseline plan tasks are implemented; the current hardening suite is
covered by 221 offline tests
(no network, no `dreamina` binary, no paid calls). Eleven of the plan's
thirteen completion-gate lines are `PASS` on measured evidence — including
Skill snapshot parity pinned to upstream
`full-aigc-skills/dreamina-skills@300bfc1`, strict per-Skill TRACE 13/13,
plugin validation, zero secret matches, and a **public-marketplace install
verified to discover all 14 Skills** via `codex debug prompt-input`.

The last two gate lines are disjunctions:

```text
read_only_runtime_contract = observed or explicitly blocked
paid_canary = separately approved or NOT_RUN
```

The read-only runtime contract is now `observed` from the installed CLI's
version and command-help snapshot. The paid canary remains `NOT_RUN`; no paid
generation has been performed. `--plan-gate` reports
`plan_gate = SATISFIED` for exactly that state:

```text
$ python3 scripts/validate_distribution_v7.py --plan-gate     # exit 0
```

### Closing the two runtime gates — pick one

**Path (a) — accept the `explicitly blocked` / `NOT_RUN` branch:**

```text
$ python3 scripts/record_gate_decision.py accept-blocked \
      --approver <your-name> --reason "<why live evidence is deferred>"
$ python3 scripts/validate_distribution_v7.py --plan-gate     # confirm exit 0
```

**Path (b) — upgrade to `observed` / `APPROVED`:**

```text
$ python3 scripts/unlock_runtime_gates.py probe     # after installing + authorizing dreamina
# then fill in docs/verification/account-readiness.md
$ python3 scripts/unlock_runtime_gates.py record-canary \
      --submit-id <real-submit-id> --approver <your-name> --observed "<what you saw>"
$ python3 scripts/validate_distribution_v7.py --require-runtime-gates   # confirm exit 0
```

Both paths are one command and require **you** — the tooling records the
decision, it never makes one. `probe` refuses to run without the CLI,
`record-canary` rejects placeholder submit IDs, and `accept-blocked`
refuses a placeholder approver. Neither command will fabricate evidence.

Full detail: [authorization decision record](docs/verification/authorization-decision.md) ·
[offline evidence](docs/verification/offline.md) ·
[Skill discovery](docs/verification/skill-discovery.md) ·
[strict TRACE](docs/verification/skill-trace.md) ·
[CLI runtime](docs/verification/dreamina-cli-runtime.md)

## Boundaries

- The installed `dreamina` CLI owns authentication and remote API behavior.
- Model names, resolutions, ratios, duration, and required flags come from current CLI help/schema.
- Generation consumes membership benefits or credits and requires explicit approval.
- Video prerequisites performed on the web are reported, not bypassed.
- Unknown results are queried by `submit_id`; submissions are not blindly retried.

## Documentation

- [Architecture](docs/Codex-Dreamina-Design-Plugin-Architecture.md) / [中文](docs/Codex-Dreamina-Design-Plugin-Architecture.zh_CN.md)
- [Technical solution](docs/Codex-Dreamina-Design-Plugin-Technical-Solution.md) / [中文](docs/Codex-Dreamina-Design-Plugin-Technical-Solution.zh_CN.md)
- [Design spec](docs/superpowers/specs/2026-09-11-codex-dreamina-design-plugin-design.md)
- [Implementation plan](docs/superpowers/plans/2026-09-11-codex-dreamina-design-plugin-implementation.md)

## Upstream skill migration

The source repository migration is complete at `full-aigc-skills/dreamina-skills`. Plugin packaging must pin a verified upstream commit rather than copying and independently editing those Skills.

## License

Apache-2.0 — see [LICENSE](LICENSE).
