## 1. 生产 MCP 与版本

- [x] 1.1 为十个项目工具增加生产分发失败测试并确认旧实现失败。
- [x] 1.2 实现 `VideoProjectRuntime` 组合根和十个默认 handler。
- [x] 1.3 将生产 MCP 连接到项目工具注册表，并验证非法请求在副作用前失败。
- [x] 1.4 建立 manifest 驱动的 MCP 版本事实源并更新版本测试与文档。

## 2. 视觉循环领域契约

- [x] 2.1 为 `VisualTargetReceipt` 和 `VisualRoundReceipt` 添加闭合 JSON Schema 与失败测试。
- [x] 2.2 实现私有、原子、可恢复的视觉循环存储和状态迁移。
- [x] 2.3 实现宿主无关 `JudgePort`、结构化 Judge 校验和评价器证据。

## 3. 图片与视频闭环

- [x] 3.1 实现首轮生成完成后的自动评价，证明不会自动创建第二个付费请求。
- [x] 3.2 实现默认至多一次、受 quote/allowance 约束的图片重试。
- [x] 3.3 将视频关键帧证据和 `VideoEvaluationService` 接入视觉轮次。
- [x] 3.4 实现停滞、重新规划、停止和诚实的远端取消语义。
- [x] 3.5 增加 Blender/DCC 预览适配端口，保持默认未配置和失败关闭。

## 4. 文档、版本和验证

- [x] 4.1 更新 Harness、MCP、架构与使用文档，说明跨客户端 Judge 和费用边界。
- [x] 4.2 修复当前三个插件本地 Skill 的严格 TRACE 问题。
- [x] 4.3 bump minor 版本并同步本仓全部 manifest，不执行远端发布。
- [x] 4.4 运行目标测试、完整测试、分发校验、TRACE 和严格 OpenSpec 验证。
