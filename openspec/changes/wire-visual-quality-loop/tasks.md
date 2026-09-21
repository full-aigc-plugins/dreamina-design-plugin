## 1. 验收基线与失败测试

- [x] 1.1 新增经生产分发入口的失败测试：断言视觉循环工具出现在工具清单中，且当前实现下该断言失败。
- [x] 1.2 新增失败测试：非法 action 与缺失必填字段在产生任何付费提交或状态写入之前返回结构化错误。
- [x] 1.3 新增失败测试：未获授权时执行首轮不产生付费请求，且循环保持可恢复状态。
- [x] 1.4 新增失败测试：产物摘要或字节数与提交时记录不一致时该轮被拒绝。
- [x] 1.5 记录基线：既有 `tests/test_visual_quality_loop.py` 全部通过（881 项基线），并确认这些测试不构成接通验收证据（其调用者全在测试内）。

## 2. 组合根与工具面

- [x] 2.1 新增独立组合根 `scripts/visual_loop_runtime.py`，持有状态根、审批提供者与生成端口工厂，对外提供注册、归属判断与调用三种能力。
- [x] 2.2 定义闭合输入的工具 Schema，action 枚举覆盖创建、锁定目标、首轮生成、重试生成、记录评审、重规划提案、重试授权、状态查询与停止（见 design 决策 7）。
- [x] 2.3 将工具表项并入生产服务的工具清单（`_tool_definitions`），并把归属判断接入统一分发分支（`DreaminaMcpTools.call`）。
- [x] 2.4 验证归属分流不会与既有视频项目工具产生名称冲突或重复注册。
- [x] 2.5 断言记录评审步骤只接受闭合的结构化评审结果，并在缺失必填字段或评分越界时失败关闭。

## 3. 付费适配器与证据映射

- [x] 3.1 实现付费生成适配器 `_ApprovedVisualLoopGeneration`，内部完整复用既有公开付费处理器 `dreamina_submit_image` 与 `dreamina_query_task`，因此审批、账本与提交防重与直接生成完全一致。
- [x] 3.2 实现产物映射：把已校验下载产物补充 `submit_id` 构成闭合五元组；写入回执前由领域存储重新计算摘要与字节数。
- [x] 3.3 断言未配置生成端口或审批被拒绝时零付费请求，且不激活任何额度。
- [x] 3.4 断言首轮评价未达标后不产生第二个付费请求，重试仅在精确指纹与信用额度上限匹配时执行。
- [x] 3.5 断言 DCC 预览轮次被拒绝走付费路径，且停止操作不声称远端任务已取消。

## 4. 可发现性

- [x] 4.1 更新 harness 技能：能力族表补入独立评审技能与视觉闭环、长片续跑两族，工作流补入闭环步骤，付费门禁补入重试的指纹与额度约束。
- [x] 4.2 修正 harness 技能表述：计数 21 本身正确（17 上游锁定 + 4 插件本地），但表中未点名 `dreamina-vision-judge`、`dreamina-auto-seedance`、`dreamina-seedance-resume`，已补入。
- [x] 4.3 新增漂移守卫测试：断言 harness 声明的技能数与 `skills/` 实际目录数一致，且三个此前漏点名的技能已出现。
- [ ] 4.4 **延后（不是遗漏）**：更新路由技能 `dreamina-design-use` 指向闭环。该技能是上游锁定技能（`skills.lock.json` 钉到 `dreamina-skills@v1.6.2` 并带逐技能 sha256），本地编辑会让 `skill_vendor check` 以非零状态失败并将在下次同步被覆盖。已在 design.md 记录：若需路由层指向，必须向上游提交，而不是在插件仓打补丁。发现路径现由 harness + `commands/dreamina-visual-loop.md` + 文档承担。
- [x] 4.5 新增命令层入口 `commands/dreamina-visual-loop.md`，按本仓既有命令的头部字段格式编写。
- [x] 4.6 更新视觉循环文档，补入 MCP 工具面、两段式轮次、付费边界与发现路径。

## 5. 一致性收尾与版本

- [x] 5.1 同步工具表与 README（中英双份）中的工具数量与清单（21 → 22），并校正 README 中陈旧的"13 pinned"上游锁定技能数为 17。
- [x] 5.2 同步分发校验中的付费工具断言（`dreamina_visual_loop` 纳入付费清单）与工具计数断言（`test_video_project_mcp.py`、`test_dreamina_mcp_server.py`、`.mcp.json` 工具数 21 → 22）。
- [x] 5.3 核对 `dreamina-vision-judge` 已在插件本地技能清单中登记，且未占用上游 `dreamina-prompt-*` 命名空间；`skill_vendor check` 退出码 0。
- [x] 5.4 bump minor 0.5.0 → 0.6.0（与变更 2 任务 5.3 同一批发布）：三平台 manifest + marketplace `ref: v0.6.0` + README 版本表 + 版本断言测试同批更新；`v0.6.0` tag 与市场仓同步紧随本提交，三件同批消除安装 404 故障模式。

## 6. 验证与归档前置

- [x] 6.1 运行目标测试与全量测试：899 项通过（基线 881 + 新增 18，含 DCC 预览拒走付费路径与审批被拒两条用例）。
- [x] 6.2 运行分发校验与技能快照校验：`validate_distribution.py` 通过、`validate_distribution_v7.py` 通过、`skill_vendor check` 退出码 0。
- [x] 6.3 运行严格 OpenSpec 校验：7 项全部通过（含归档后新建的 `video-project-runtime` 与 `visual-quality-loop` 两个主规格）。归档暴露出前者遗留的 Purpose 不足 50 字符，已按规范直接修正主规格 Purpose 使其通过严格校验。
- [x] 6.4 运行 TRACE 评测：`PASS`，21/21 技能通过，退出码 0。
- [x] 6.5 归档 `integrate-visual-quality-loop`：已归档为 `2026-09-21-integrate-visual-quality-loop`，`video-project-runtime`（+3 需求）与 `visual-quality-loop`（+7 需求）已同步进 `openspec/specs/`。
- [x] 6.6 记录验证结论：本次全部为**本地自动验证**——899 项测试、分发校验、技能供应链校验、TRACE、严格 OpenSpec。**未执行任何真实付费提交**，因此付费路径的端到端行为（真实 submit_id、真实下载产物、真实额度消耗）仍是 `NOT_RUN`，需按用户单独安排的金丝雀验收覆盖。

## 实施期发现的修正（已回写 design.md）

- 决策 7：轮次拆分为「生成」与「记录评审」两个宿主可见步骤。融合调用的 `_run` 在 stdio MCP 服务中结构上不可达——这才是闭环此前无法接线的根因。
- 决策 3 补充：一次轮次实现上是「提交 → 轮询 → 下载」，因为 `ImageService.submit` 以 `--poll 0` 提交只返回 `submit_id` 而不落盘，而回执要求本地文件加摘要。
- 决策 5 补充：路由技能是上游不可变资产，发现路径只能走插件本地界面。

## 未决事项

- 宿主侧 `JudgePort` 参考适配器尚未实现：`record_judgement` 已接受结构化结论，但"起 fresh-context 视觉子代理"仍由宿主承担。按 design 决策 3，这属宿主层交付物。
