# Codex Dreamina Design 插件技术方案

## 技术决策

围绕已安装的 `dreamina` CLI 构建 Skills-first 兼容插件，共用 subprocess 适配器、能力快照、批准门禁、操作台账和产物验证器。

## 迁移映射

现有 13 个 Skill 目录从 `jimeng-*` 机械迁移为 `dreamina-*`，`dreamina-cli` 保持规范入口。目录名、frontmatter、链接、示例、README、GitHub 路径和安装命令必须同时变化。验证器拒绝残留 `jimeng-` 标识，但允许在产品说明中解释旧名称。

## 契约

- `CapabilitySnapshot`：CLI version/commit 和当前 schema；若已安装 CLI 没有 `schema` 子命令，则使用 command-help 快照。
- `GenerationRequest`：模式、Prompt、参考素材、模型 token、分辨率、比例、时长和数量。
- `ApprovalReceipt`：精确请求指纹及费用/积分确认。
- `OperationReceipt`：Session、submit ID、状态、时间和 required action。
- `ArtifactReceipt`：本地路径、校验和、媒体元数据和来源 submit ID。

## 测试

通过已安装 CLI 的 version/help/schema 生成无消费快照；合成 fixture 覆盖认证、权限、升级、校验、查询、失败、取消和下载。消耗积分的 canary 不进入普通 CI。
