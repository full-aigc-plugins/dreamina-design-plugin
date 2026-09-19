## Context

这些插件最初只面向 Codex，因此文档名和内部说明使用 `codex-<product>`。当前仓库已经通过 `.codex-plugin/plugin.json`、`.zcode-plugin/plugin.json` 与 `kimi.plugin.json` 支持多个宿主，公共身份必须与宿主适配层解耦。

## Goals / Non-Goals

### Goals

- 公共文件名、标题、技能名和插件引用使用产品/能力名称。
- 真实的 Codex 专属路径、命令、环境变量和兼容协议保持准确。
- 所有重命名文件的站内链接同步更新。

### Non-Goals

- 不重命名 `.codex-plugin` 或移除 `+codex.<date>`。
- 不改变 GitHub 仓库名、已发布 tag 或外部供应商接口。
- 不修改历史归档中的原始事实，除非其被当前入口直接引用。

## Decisions

### 按语义而不是字符串全局替换

仅当名称表示公共插件、技能或文档身份时移除前缀。表示 Codex 宿主、Codex CLI、Codex manifest、缓存目录或迁移兼容协议时保留原名。

### 当前公共名称是唯一推荐名称

文档不继续宣传旧的 `codex-*` 公共别名。若运行时存在真实兼容标识，则在兼容章节明确标记，而不是把旧名保留为主名称。
