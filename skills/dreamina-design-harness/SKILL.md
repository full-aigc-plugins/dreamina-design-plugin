---
name: dreamina-design-harness
description: Dreamina Design invocation spec for WorkBuddy - the image/video generation CLI (trusted_cli), text2image/image2image/text2video/image2video families across cli/opencli/prompt variants, shot annotation, video evaluation, and the paid submit-once gates with real submit_id verification. Read this before any Dreamina generation task.
---

# 即梦设计调用规范（智能体通用）

执行通道：本插件随附的生成 CLI（`scripts/trusted_cli.py` 为受信入口；`dreamina-cli` 技能含完整用法）。
后端：**即梦（Dreamina），付费动作**，submit-once 门禁与真实 submit_id 验收是硬约束。

## 1. 能力族（22 个技能已随插件分发）

| 族 | 技能 |
|---|---|
| 文生图 / 图生图 | `dreamina-cli-text2image` / `image2image`（+ `opencli-*` 变体 + `prompt-*` 提示词口径） |
| 文生视频 / 图生视频 | `dreamina-cli-text2video` / `image2video`（同上三变体） |
| 视频生产流 | `dreamina-video-production`（生产编排）、`dreamina-shot-annotator`（镜头标注） |
| 评测 | `dreamina-video-evaluator`（视频质量评测）、`dreamina-vision-judge`（目标图 vs 候选图的独立评审） |
| 视觉质量闭环 | `dreamina_visual_loop` MCP 工具：锁定目标 → 一轮付费生成 → **宿主**独立评审 → 受额度约束的一次重试 |
| 目标获取 | `dreamina-visual-target`：用户没给目标图时取得目标（基线精修优先），反演绎纪律与付费门禁 |
| 长片续跑 | `dreamina-auto-seedance`（自动续跑）、`dreamina-seedance-resume`（断点恢复） |
| 入口 | `dreamina-design-use` / `dreamina-cli` |

**边界（如实）**：设计套件专注**图与视频**，无独立音频生成技能；**声音能力在"即梦画布"套件**
（`dreamina-canvas-generate-audio`），需要配音/音效时转画布团队。

## 2. 付费门禁（不可绕）

- 每次**付费提交**都要：用户明确授权 → 记录真实 `submit_id` → 凭据核对（回执/下载的 SHA-256 等）。
- `submit-once`：一次授权一次提交；失败恢复走技能里的恢复路径，**不得自动重试付费提交**。
- 预算上限由用户给定；报价缺失时（`QUOTE_UNAVAILABLE`）停下问，不猜。
- 视觉质量闭环的重试另受**精确请求指纹 + 信用额度上限**约束：首轮评价未达标只进入等待授权，
  **不会**自行提交第二次；授权绑定的指纹或额度与实际请求不一致时一律拒绝。
- 目标生成同样是一次付费提交，须经用户授权；无授权时停下问，不静默降级为无目标迭代。

## 3. 标准工作流

1. 明确产物类型（图/视频）与提示词基线（`prompt-*` 技能）。
2. 走 `cli` 或 `opencli` 族执行生成（参数以技能文档为准）。
3. 视频过 `dreamina-video-evaluator`；镜头类需求用 `shot-annotator`；图像对标迭代用 `dreamina-vision-judge`。
4. 要迭代到与目标图一致时走 `dreamina_visual_loop`：用户没给目标图时先用 `dreamina-visual-target`
   取得目标（目标生成是**付费提交**，须授权；已有产物走基线精修，不另起方向），`lock_target` 锁定 →
   `run_first_round` 执行一轮付费生成 → 把返回的证据交 `dreamina-vision-judge` 评审 → `record_judgement`
   记录结论。一轮由**两个调用**组成（生成、记录评审），因为评审必须由宿主的全新上下文子代理完成
   ——宿主侧的适配配方见 [references/judge-port-adapter.md](references/judge-port-adapter.md)。
5. 交付：产物路径、submit_id、验证凭据、消耗与剩余预算、未验证项。

交付回执至少保留真实提交标识和验证状态，例如：

```json
{"submit_id":"<provider-submit-id>","artifact_verified":true,"paid_retry":false}
```

## 4. 纪律

- CLI 输出与回执是事实来源；叙述与输出冲突以输出为准。
- 修复实现委托给 worker 子代理时，遵守
  [references/repair-delegation.md](references/repair-delegation.md)：
  worker 全新空上下文、只给高层指令、只实现不验收，实现与评审角色分离。
- 与**图片工厂**（Codex 后端）是双轨：用户没指定后端时，问清按哪个账本走。
