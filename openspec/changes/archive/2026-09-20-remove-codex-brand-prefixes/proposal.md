## Why

插件已经同时适配 Codex、ZCode 与 Kimi，但公共文档、技能引用和文件名仍残留早期 `codex-` / `Codex-` 前缀，会把跨宿主能力误解为 Codex 专属能力，并导致新旧命名并存。

## What Changes

- 将公共架构文档、技术方案、技能引用和插件引用迁移到无宿主前缀的当前名称。
- 更新所有受影响的相对链接、示例、图表和验证断言，避免重命名后出现死链。
- 保留 `.codex-plugin`、Codex CLI、Codex build metadata 和明确的兼容协议等真实宿主专属名称。
- 增加命名审计，防止跨宿主公共标识重新引入 `codex-` 前缀。

## Capabilities

### New Capabilities

- `cross-host-plugin-identity`: 定义跨宿主插件的公共命名与 Codex 专属例外边界。

### Modified Capabilities

None.

## Impact

影响 README、docs、技能/命令引用、测试与文档链接；不修改三端 manifest 的宿主约定，不改变运行时 API、技能锁定来源或已发布 tag。
