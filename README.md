# Codex Dreamina Design Plugin

<img src="assets/logo.png" alt="Dreamina Design logo" width="128">

> Compatibility foundation for Dreamina CLI image and video workflows.

[English](README.md) | [简体中文](README.zh-CN.md)

## Status and purpose

**Production-ready release 0.2.1.**

The offline implementation gate is satisfied, but production acceptance is
tracked separately in
[`docs/superpowers/plans/2026-09-12-production-readiness-hardening.md`](docs/superpowers/plans/2026-09-12-production-readiness-hardening.md).
The minimum-specification canary, local installation, input-file containment,
official validation, final security review, remote publication, and fresh
public-marketplace reinstall are complete.

```text
Creative intent -> prompt contract -> live CLI capability discovery
                -> explicit generation approval -> submit once
                -> query by submit_id -> validate/download artifacts
```

All seven baseline plan tasks are implemented; the current hardening suite is
covered by 225 offline tests. All thirteen baseline completion-gate lines now
have measured evidence — including
Skill snapshot parity pinned to upstream
`full-aigc-skills/dreamina-skills@e8ae588`, strict per-Skill TRACE 13/13,
plugin validation, zero secret matches, and a **public-marketplace install
verified to discover all 14 Skills** via `codex debug prompt-input`.

The runtime gate lines are:

```text
read_only_runtime_contract = observed or explicitly blocked
paid_canary = separately approved or NOT_RUN
```

The read-only runtime contract is `observed`. The authorized canary is
`APPROVED` and reached `success`; its artifact and cost evidence are recorded.
Both the baseline and strict runtime gates pass:

```text
$ python3 scripts/validate_distribution_v7.py --plan-gate
$ python3 scripts/validate_distribution_v7.py --require-runtime-gates
```

### Runtime evidence

```text
$ python3 scripts/unlock_runtime_gates.py status
$ python3 scripts/validate_distribution_v7.py --require-runtime-gates
```

The paid production path requires protected CLI enrollment plus a server-side
native confirmation dialog whose default action is Cancel.

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
Version 0.3.0 exposes eleven typed MCP tools covering the official Dreamina CLI
lifecycle: status, verified installation/update, OAuth flows, account readiness,
all documented image/video modes, task query/list/download, Session CRUD, and
bounded redacted log diagnosis. High-risk and paid operations require explicit
server-side confirmation; arbitrary shell or argv execution is not exposed.
