## Purpose

保证 Dreamina 视频项目工具在所有受支持宿主中不仅可被发现，而且能够经过同一套生产 MCP 分发、校验、审批和持久化服务真实执行。

## ADDED Requirements

### Requirement: 可发现的项目工具必须可调用
系统 SHALL 为公开的十个视频项目工具注册生产处理器，并在请求进入领域服务前执行闭合 Schema 与 action 规则校验。

#### Scenario: 调用项目状态工具
- **WHEN** MCP 客户端调用 `dreamina_video_project` 的 `runtime_status` action
- **THEN** 系统返回结构化运行时状态，而不是 `unknown tool` 或 `no registered handler`

#### Scenario: 非法参数失败关闭
- **WHEN** 项目工具请求包含未声明字段或非法 action
- **THEN** 系统在任何文件写入、外部进程或付费提交发生前拒绝请求

### Requirement: 项目工具保持原有安全边界
系统 MUST 保持原有审批、可信媒体工具、授权目录、不可扩张 allowance、提交防重和查询同一 `submit_id` 规则。

#### Scenario: 付费执行必须有 allowance
- **WHEN** 客户端要求执行视频生成批次但未提供可验证的激活 allowance
- **THEN** 系统拒绝提交，且不调用供应商

### Requirement: 版本必须来自单一事实源
MCP 初始化返回的版本 SHALL 与插件发布清单的基础语义版本一致，测试与文档不得固定旧版本。

#### Scenario: MCP 初始化
- **WHEN** 客户端初始化发布版本的 MCP 服务
- **THEN** `serverInfo.version` 等于插件 manifest 去除宿主构建后缀后的版本

