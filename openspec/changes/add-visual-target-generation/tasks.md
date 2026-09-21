## 1. 失败测试与验收基线

- [x] 1.1 新增测试：`test_generated_target_locks_and_drives_a_full_round`——生成类目标（`source.kind=generated`）锁定后走完整一轮（生成 → 评审记录），轮次回执绑定锁定时的目标摘要。
- [x] 1.2 新增测试：`test_generated_target_gets_the_same_path_validation`——生成类目标落在授权目录之外时与用户提供目标同样被拒绝。
- [x] 1.3 新增测试：`test_tool_surface_has_no_auto_target_generation_action`——工具面不存在任何"生成目标"动作，目标生成只能走独立审批的付费提交，循环内无自动生成路径（配合既有审批被拒用例，未授权时零付费）。
- [x] 1.4 新增测试：`test_generated_source_provenance_is_recorded`——回执记录 `kind/provider/submit_id` 且通过闭合校验（`receipt_fingerprint` 存在）。
- [x] 1.5 记录基线：既有目标锁定与回执测试全部通过；实测确认它们只覆盖 `user_supplied`，不覆盖生成类目标——新增四项测试补齐该缺口（领域层 `lock_target` 本就接受任意来源映射，故测试直接转绿，作用是把生成类目标锁为一等公民并防回归）。

## 2. 目标获取流程（技能层）

- [x] 2.1 新技能 `dreamina-visual-target`：三条获取路径（用户已提供 / 基线精修 / 全新创作），基线精修明确"以当前产物为输入生成改进版，收敛不另起方向"。
- [x] 2.2 反演绎纪律章节：目标须为可逐项对照的成品呈现，含正反两例；明确不维护禁用词表、约束意图类别。
- [x] 2.3 来源记录说明：`source.kind` 区分用户提供/系统生成/既有产物，生成类记录真实 `submit_id`。
- [x] 2.4 失败关闭路径：无授权、审批被拒、能力不可用三种情形停下询问，不静默降级为无目标迭代（另见 examples/boundary-refusal.md）。
- [x] 2.5 目标质量自检：同呈现类别、可比画幅、逐区域可对照三项核对。
- [x] 2.6 `examples/happy-path.md`（端到端）与 `examples/boundary-refusal.md`（缺授权/能力不可用/目标与候选不可比）。

## 3. 委托纪律（技能层）

- [x] 3.1 `skills/dreamina-design-harness/references/repair-delegation.md` §1：全新空上下文、禁止继承历史、禁止 fork。
- [x] 3.2 §2：指令只含目标、当前状态与用户诉求；不派发逐条任务、不把评审清单当任务单。
- [x] 3.3 §3：worker 只实现不验收；编排者负责可运行性验证与非视觉问题修正。
- [x] 3.4 §4：禁止 worker 递归发起循环与付费提交；同一轮内不产生额外付费。
- [x] 3.5 §5：实现与评审角色分离；实现者自评不作为有效验收证据。
- [x] 3.6 文末委托指令模板（可直接复用）。

## 4. harness 与文档

- [x] 4.1 harness 标准工作流第 4 步补入目标获取及其付费边界（无目标先走 `dreamina-visual-target`，目标生成须授权）。
- [x] 4.2 harness §4 纪律补入委托与角色分离表述并链接 `references/repair-delegation.md`。
- [x] 4.3 评审技能补 "Target provenance" 节：目标来源可能为生成，评审方法不变。
- [x] 4.4 循环文档新增"目标从哪来"与"修复实现的委托纪律"两节。
- [x] 4.5 真实性核对（grep 实测）：新增/改动文档无"自动生成目标""自动重试"类声称；目标生成表述为"付费提交、须授权"；重试表述为"至多一次、受额度约束"。

## 5. 供应链与版本

- [x] 5.1 `dreamina-visual-target` 已登记进 `plugin-local-skills.json`（5 项本地技能），`skill_vendor check` 退出码 0，无"未声明技能"告警。
- [x] 5.2 命名空间核对：上游未占用 `dreamina-visual-target`，未使用 `dreamina-prompt-` 前缀，与 17 个上游锁定技能零重名。
- [x] 5.3 bump 版本 0.5.0 → 0.6.0：三平台 manifest（`.codex-plugin` 0.6.0+codex.20260922 / `.zcode-plugin` / `kimi.plugin.json`）、marketplace manifest（version + `ref: v0.6.0` + CDN 图标 URL）、README 中英双份（badge/当前版本/两条特性行/安装 `--ref`）、版本断言测试 ×3、架构文档中英四处、循环文档版本头，一次同批完成；`v0.6.0` tag 与市场仓同步紧随本提交。

## 6. 验证

- [x] 6.1 全量测试 **903 项通过**（变更 1 后基线 899 + 本变更新增 4 项生成类目标测试）。
- [x] 6.2 `validate_distribution.py` 通过；`validate_distribution_v7.py` 通过（`skill_snapshot_status: PASS`）；`skill_vendor check` 退出码 0。
- [x] 6.3 TRACE：`PASS`，22/22 技能通过（含新技能 `dreamina-visual-target`），退出码 0。
- [x] 6.4 `openspec validate --all --strict`：7 项全部通过（两个变更 + 五个主规格）。
- [x] 6.5 验证结论：本次全部为**本地自动验证**。目标生成的端到端真实付费行为（真实 submit_id 产目标、真实审批交互）为 `NOT_RUN`，与循环本身的付费验收一同待用户单独安排。

## 实施期决定（已回写 design.md）

- Open Question 落定：目标获取为**独立技能** `dreamina-visual-target`（评审技能自我声明"纯评估、不重生成"，并入会违反其边界）；委托纪律落在 harness 的 `references/`（编排契约归契约层）。
