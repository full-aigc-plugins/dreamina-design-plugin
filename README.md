# Codex Dreamina Design Plugin

> Design-stage image and video generation workflows for the Dreamina CLI.

[English](README.md) | [简体中文](README.zh-CN.md)

## Status and purpose

`codex-dreamina-design` is not implemented yet. It will package the renamed `dreamina-*` prompt and CLI Skills for image generation, image editing, text/image/frame/multimodal video generation, sessions, history, asynchronous query, and download.

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

The current source repository is `full-aigc-skills/jimeng-skills` with 13 Skills. A prerequisite change will rename it to `dreamina-skills`, migrate every `jimeng-*` identity and reference to `dreamina-*`, preserve behavior through a mapping ledger, and synchronize the current Dreamina CLI contract before packaging.
