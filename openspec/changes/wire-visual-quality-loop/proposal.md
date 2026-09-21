## Why

视觉质量闭环已在 `integrate-visual-quality-loop` 中完整实现：498 行领域代码、3 个闭合 JSON Schema、881 项测试全部通过。但它没有任何生产调用方。实测证据：`VisualLoopService` 的全部 7 个调用者都在 `tests/test_visual_quality_loop.py`，`JudgePort` 的调用者为 0，`VisualLoopStore` 的 2 个调用者也都在测试中；`VideoProjectRuntime` 只注册十个视频工具；`dreamina-design-harness`、`dreamina-design-use`、`dreamina-video-production` 与 `commands/` 对 vision / judge / 视觉闭环的提及次数均为 0。

结果是"测试全绿但功能不可达"：照 `dreamina-design-harness` 走的智能体永远不会触达闭环，随插件分发的 `dreamina-vision-judge` 技能也不会被加载。已投入的领域实现、Schema 与测试无法转化为用户可见能力，而 dream-loop 方法论中对齐目标图、独立评审、停滞重规划的价值因此全部沉没。

## What Changes

- 新增独立组合根 `scripts/visual_loop_runtime.py`，以 `VideoProjectRuntime` 的形状提供 `registry()` / `handles()` / `call()`，承载视觉循环工具的装配；**不**把循环并入 `VideoProjectRuntime`。
- 新增闭合 MCP 工具 `dreamina_visual_loop`，action 覆盖 `create` / `lock_target` / `run_first_round` / `run_retry` / `propose_retry` / `status` / `stop`，并注册进生产 stdio 服务的工具表与分发路径。
- 实现 `GenerationPort` 的默认适配器，复用现有 `ImageService` 付费链路（capability snapshot → reference policy → `ApprovalGuard` → `OperationLedger` → `submit`），并把返回产物映射为 `VisualRoundReceipt` 要求的闭合五元组；适配器 MUST NOT 自建付费通道。
- 在 harness、路由技能与命令层公开该闭环，使其可被智能体发现；同步修正 harness §1 声明的技能数与 `skills/` 实际目录数不一致的陈旧表述。
- 新增真实分发回归测试（经 `DreaminaMcpTools.call()` 而非直连 service），并同步工具表、README 与分发校验断言。

## Capabilities

### New Capabilities
- `visual-loop-runtime`: 视觉循环的生产装配与 MCP 工具面——组合根、工具 Schema 与 action 语义、`GenerationPort` 默认适配器与付费边界、跨介质（image / video / dcc_preview）的入口一致性，以及循环在 harness / 路由 / 命令层的可发现性。

### Modified Capabilities
（无。领域闭环的状态机、回执契约与额度语义在本变更中保持不变；"可达性"与"付费通道唯一性"作为装配层行为记入新能力 `visual-loop-runtime`。

`visual-quality-loop` 能力目前只由尚未归档的 `integrate-visual-quality-loop` 声明，尚未同步进 `openspec/specs/`。本变更刻意不为它写增量，以避免两个变更之间的归档顺序依赖；若确需修改该能力的既有需求，应在前者归档后另开变更。）

## Impact

- 新增：`scripts/visual_loop_runtime.py`、`tests/test_visual_loop_mcp_tool.py`、`commands/dreamina-visual-loop.md`。
- 修改：`scripts/dreamina_mcp_server.py`（工具表拼接与分发分支）、`scripts/visual_quality_loop.py`（仅新增适配器所需的最小接缝，不改状态机）、`skills/dreamina-design-harness/SKILL.md`、`skills/dreamina-design-use/SKILL.md`、`docs/visual-quality-loop.zh_CN.md`、`README.md` 与 `README.zh-CN.md` 的工具表。
- 依赖：复用既有 `ImageService`、`VideoService`、`ApprovalGuard`、`OperationLedger`、`VideoEvaluationService`；不引入新的供应商 SDK，不新增第三方依赖。
- 兼容性：纯增量。既有 21 个技能与 11 + 10 个 MCP 工具保持原状；未知循环状态目录对旧版本不可见。
- 非目标：不做远端发布、不做真实付费验收、不移植 dream-loop 的 Fal 执行器或本地预览服务器、不按宿主订阅等级区分工作流。
- 前置依赖：本变更假定 `integrate-visual-quality-loop` 的领域实现已在工作树中；该变更尚未归档，`visual-quality-loop` 能力尚未同步进 `openspec/specs/`。
