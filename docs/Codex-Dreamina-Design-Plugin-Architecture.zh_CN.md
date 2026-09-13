# Codex Dreamina Design 插件架构

## 0.3.0 全自动化层

stdio MCP 服务提供 11 个闭合 Schema 工具，覆盖 CLI 状态、可信安装更新、仅内存
OAuth 流程、账户就绪检查、8 种生成模式、任务查询/列表/下载、Session CRUD 和
脱敏日志诊断。领域服务只构造固定 argv；MCP 不开放任意 Shell 或命令执行。高风险
操作除 MCP 审批元数据外，还必须经过服务端原生确认。

```mermaid
flowchart LR
    Codex --> MCP[11 个强类型 MCP 工具]
    MCP --> Services[领域服务]
    Services --> Guard[原生确认与请求防护]
    Services --> Adapter[可信 argv-only 适配器]
    Adapter --> CLI[Dreamina CLI]
    Services --> Ledger[操作账本]
    Services --> Verify[产物与日志校验]
```

> 功能架构已实现，生产加固进行中。重新验证日期 2026-09-13。

## 系统上下文

```mermaid
flowchart LR
    User[用户] --> Router[意图路由]
    Router --> Prompt[Prompt Skills]
    Router --> Capability[CLI 能力快照]
    Prompt --> Request[生成请求]
    Capability --> Request
    UI[Codex MCP 弹窗 + 服务端原生弹窗] --> Approval[单次批准门禁]
    Request --> Approval
    Approval --> Intent[持久化 SUBMITTING 意图]
    Intent --> CLI[绝对路径 + SHA-256 可信 CLI]
    CLI --> Ledger[submit_id 台账]
    Ledger --> Query[有界查询]
    Query --> Download[CLI 下载到批准根目录]
    Download --> Artifact[已验证产物]
```

## 限界上下文

| 上下文 | 职责 |
|---|---|
| Prompt | 表达内容，不虚构不支持参数 |
| Capability | 实时模型、分辨率、比例、时长和参数 |
| Generation | 规范图片/视频请求与请求指纹 |
| Approval | 提交前确认精确费用和范围 |
| Operation | submit ID、终态、恢复和历史 |
| Artifact | 下载、校验和、媒体元数据和来源 |

## 运行规则

CLI 是远程事实权威，插件不实现 Dreamina 私有 API。每个生成请求只提交一次；超时进入 `Unknown`，通过查询/历史对账。产物验证完成后才可宣布任务完成。

## Skill 拓扑

`dreamina-skills` 是可复用 Skill 事实源。本插件完整打包各 Skill 目录，
并通过 `skills/.upstream-commit` 固定提交后逐文件验证字节一致性。

## 安全

manifest、日志、Prompt、台账和产物中不得出现凭据。本地参考文件必须通过
批准根目录、普通文件、类型和大小校验。付费动作通过配置了
`approval_mode: prompt` 的 MCP 工具暴露，并额外要求服务端原生确认弹窗；
CLI 身份只能来自独立注册的私有信任配置。
