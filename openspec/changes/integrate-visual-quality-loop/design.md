## Context

现有十一项原子 MCP 工具已经具备审批、提交意图、同一 `submit_id` 查询和授权目录保护。十个视频项目工具只有定义和可注入注册表，生产 stdio 服务没有默认 handler。视频项目域已经包含项目存储、参考视频分析、镜头语义、重设计、报价、allowance、批次执行、评价、合成和导出服务。

本地未推送提交新增了 `dreamina-vision-judge` Skill。它定义评审方法，但不承担运行时状态或供应商调用。

## Goals / Non-Goals

**Goals:**

- 用一个生产运行时组合根装配十个项目工具。
- 将视觉目标、轮次、Judge 和停滞状态建模为领域回执，而不是 Skill 隐式上下文。
- 图片与视频共享循环控制，继续复用各自的媒体验证和付费执行服务。
- 所有自动重试都受不可扩张 allowance 和提交防重保护。

**Non-Goals:**

- 不复制 Dream Loop 的 Fal 执行器或本地预览服务器。
- 不按宿主订阅等级区分 Plus/Pro。
- 不在没有真实账户授权时执行付费验收。
- 本变更不发布、不安装，也不静默推送远端。

## Decisions

### 1. 生产组合根与领域服务分离

新增 `VideoProjectRuntime` 作为十个工具的生产组合根，`VideoProjectMcpTools` 继续只负责 Schema/action 校验和分发。这样测试可以注入替身，生产环境则获得完整默认 handlers。

### 2. Judge 使用端口而非宿主分支

定义 `JudgePort` Protocol 和闭合 `JudgeResult` 契约。宿主负责取得独立模型评价，插件只验证并持久化结构化结果。当前 prompt Judge Skill 提供 rubric；领域代码不导入任何客户端 SDK。

### 3. 循环控制器不直接绕过现有服务

视觉循环只协调目标、轮次和决策。付费提交仍进入现有 Image/Video Service、ApprovalGuard、OperationLedger 和 allowance。视频测量继续由 `VideoEvaluationService` 完成。

### 4. 单轮评价和自动重试分离

首轮完成后可以自动评价；是否提交重试由一个单独的 allowance 消费动作决定。默认 `max_attempts=2`，没有预报价请求时返回等待审批。

### 5. 终止采用诚实语义

循环状态的 `stopped` 仅阻止新提交。已有远端任务仍以原 `submit_id` 查询，不声称供应商取消成功。

### 6. 版本由 manifest 动态读取

MCP 初始化从 `.codex-plugin/plugin.json` 读取版本并去除 `+codex.*` 后缀；打包缺失 manifest 时失败关闭，而不是保留硬编码版本。

## Risks / Trade-offs

- [生产 handler 依赖可信 CLI 和媒体工具] → 运行时状态明确返回缺少项，只有实际 action 才加载相关依赖。
- [Judge 由宿主提供，可能伪造高分] → 保存评价器身份并让可信媒体门禁拥有最终否决权。
- [多轮循环增加费用] → 默认一次重试、不可扩张 allowance、每轮独立 reservation。
- [已有本地未推送 Skill 未 bump 版本] → 本变更统一纳入下一次 minor 版本，不改写原提交。
- [供应商不支持取消] → 停止本地循环并保留远端对账状态。

## Migration Plan

1. 先接通十个项目工具并增加生产 `_handle` 回归测试。
2. 引入新回执 Schema 和循环存储，不迁移现有 operation/project 数据。
3. 增加单轮评价和默认一次重试策略。
4. 接入视频关键帧与现有评价服务。
5. 最后开放重新规划和 DCC 适配器扩展点。
6. 回滚时保留新回执文件；旧版插件忽略未知目录，已有 `submit_id` 仍可通过原子查询工具恢复。
