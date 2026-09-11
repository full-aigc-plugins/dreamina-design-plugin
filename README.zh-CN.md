# Codex Dreamina Design 插件

> 面向 Dreamina CLI 的图片与视频生成工作流，目前处于设计阶段。

[English](README.md) | [简体中文](README.zh-CN.md)

## 状态与定位

`codex-dreamina-design` 尚未实现。它将打包更名后的 `dreamina-*` Prompt 和 CLI Skills，覆盖图片生成、图片编辑、文生/图生/首尾帧/多模态视频、Session、历史、异步查询和下载。

```text
创作意图 -> Prompt 契约 -> 实时发现 CLI 能力
        -> 明确批准生成 -> 单次提交
        -> 按 submit_id 查询 -> 验证/下载产物
```

## 边界

- 已安装的 `dreamina` CLI 负责认证和远程 API。
- 模型、分辨率、比例、时长和必填参数来自当前 CLI help/schema。
- 生成会消耗会员权益或积分，必须明确批准。
- 网页端首次视频生成等前提只报告，不绕过。
- 结果不确定时按 `submit_id` 查询，不盲目重试提交。

## 文档

- [Architecture](docs/Codex-Dreamina-Design-Plugin-Architecture.md) / [中文](docs/Codex-Dreamina-Design-Plugin-Architecture.zh_CN.md)
- [Technical solution](docs/Codex-Dreamina-Design-Plugin-Technical-Solution.md) / [中文](docs/Codex-Dreamina-Design-Plugin-Technical-Solution.zh_CN.md)
- [设计规格](docs/superpowers/specs/2026-09-11-codex-dreamina-design-plugin-design.md)
- [实施计划](docs/superpowers/plans/2026-09-11-codex-dreamina-design-plugin-implementation.md)

## 上游技能迁移

当前事实源是含 13 个 Skills 的 `full-aigc-skills/jimeng-skills`。前置变更会把它更名为 `dreamina-skills`，用映射台账将所有 `jimeng-*` 身份和引用迁移为 `dreamina-*`，保持行为，并在打包前同步当前 Dreamina CLI 契约。
