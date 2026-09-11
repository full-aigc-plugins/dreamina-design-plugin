# Codex Dreamina Design Plugin

<img src="assets/logo.png" alt="Dreamina Design logo" width="128">

> Compatibility foundation for Dreamina CLI image and video workflows.

[English](README.md) | [简体中文](README.zh-CN.md)

## Status and purpose

`codex-dreamina-design` now has a validated compatibility manifest, marketplace metadata, brand assets, legal documents, tests, and implementation directories. The generation Skills and CLI runtime adapter remain implementation work.

```text
Creative intent -> prompt contract -> live CLI capability discovery
                -> explicit generation approval -> submit once
                -> query by submit_id -> validate/download artifacts
```

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
