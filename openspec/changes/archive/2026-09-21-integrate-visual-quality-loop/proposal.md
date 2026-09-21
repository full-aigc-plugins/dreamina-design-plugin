## Why

Dreamina Design 目前公开了十个视频项目 MCP 工具，但生产 stdio 服务只实现了原有十一项原子工具的分发，导致工具可发现却不可调用。同时，现有生成链缺少统一的视觉目标、跨客户端独立评审和受预算约束的迭代状态，无法安全承载 Dream Loop 式质量闭环。

## What Changes

- 接通十个视频项目工具的生产 handler 注册、服务装配和端到端 MCP 测试。
- 以插件发布版本为唯一事实源，统一 manifest、MCP `serverInfo`、测试和文档版本。
- 新增 `VisualTargetReceipt`、`VisualRoundReceipt` 与持久化视觉循环状态。
- 新增单轮生成后的自动评价；评价本身不得静默提交下一次付费生成。
- 新增受整批 allowance 约束的图片视觉循环，默认至多一次预报价重试。
- 将现有视频关键帧证据和 `VideoEvaluationService` 接入同一视觉循环决策。
- 新增宿主无关的 `JudgePort`，支持 Codex、Claude Code、ZCode、Kimi 及其他 MCP 客户端提供结构化 Judge 结果。
- 新增停滞检测、显式重新规划、多轮预算和 DCC 预览扩展点；默认策略保持失败关闭。
- 明确“终止”只阻止后续提交；已提交但供应商不支持取消的任务继续以原 `submit_id` 对账。

## Capabilities

### New Capabilities

- `visual-quality-loop`: 视觉目标、轮次回执、独立评审、费用受控重试、停滞处理和恢复语义。
- `video-project-runtime`: 十个视频项目 MCP 工具的生产装配、版本事实源和真实调用契约。

### Modified Capabilities

None.

## Impact

影响 MCP 工具分发、视频项目服务装配、图片和视频生成 Harness、JSON Schema、测试、发布版本和用户文档。不会改变现有十一项原子 MCP 工具的名称和请求结构，也不会自动执行未审批的付费请求、发布或安装。
