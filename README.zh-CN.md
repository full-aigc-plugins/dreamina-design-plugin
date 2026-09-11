# Codex Dreamina Design 插件

<img src="assets/logo.png" alt="Dreamina Design Logo" width="128">

> 面向 Dreamina CLI 图片与视频工作流的 Codex 兼容基础。

[English](README.md) | [简体中文](README.zh-CN.md)

## 状态与定位

`codex-dreamina-design` 现已具备经过验证的兼容 manifest、Marketplace 元数据、品牌资产、Legal 文档、测试和实施目录。生成 Skills 与 CLI 运行适配器仍属于后续实现。

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

事实源迁移已完成，当前仓库为 `full-aigc-skills/dreamina-skills`。插件打包必须固定经过验证的上游提交，不能复制后再独立修改 Skill 正文。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
