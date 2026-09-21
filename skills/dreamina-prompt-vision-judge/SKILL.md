---
name: dreamina-prompt-vision-judge
description: Compare a candidate image against a target image using a fresh-context vision subagent and a 4-dimension rubric (composition / lighting / materials / details, total /10). Use when the user asks for visual evaluation against a target image, wants to score image fidelity, or wants a structured gap list to drive the next iteration of an image-generation prompt; mentions "视觉评审", "视觉对比", "视觉打分", "对标", "目标图对比", "target.png 对比", "看看哪里不像", "和目标图差距", "vision judge", "vision evaluation", "image rubric", "compare candidate to target", "score image fidelity". Sits next to `dreamina-prompt-text2image` / `-image2image` / `-text2video` / `-image2video` — same prompt layer, different output. NEVER confuse with `dreamina-video-evaluator` (the latter judges measured video clip gates against design intent for the Dreamina video project; this skill judges two still images against each other).
license: Complete terms in LICENSE.txt
---

# dreamina-prompt-vision-judge — 即梦图像视觉评审

把候选图与目标图交给 **fresh-context vision subagent**，按 4 维 rubric 输出
结构化打分（/10）与可执行差距清单。本技能是**纯评估**，不修改生成参数、
不重生成、不写回 saved draft、不消耗 dreamina-canvas 积分。

## When to use this skill

Use when the user:

- 想看一张刚生成图 vs 一张参考图的差距
- 想让另一个 LLM 模型"客观打分"当前生成
- 想拿到一份可执行的"下一轮改 prompt 的差距清单"
- 提到关键词：「视觉评审」「视觉对比」「视觉打分」「对标」「目标图对比」
  「target.png 对比」「看看哪里不像」「和目标图差距」
  「vision judge」「vision evaluation」「image rubric」
  「compare candidate to target」「score image fidelity」

Do NOT use this skill for:

- 写或优化 image 提示词 → `dreamina-prompt-text2image` / `-image2image`
- 执行生成或下载产物 → `dreamina-cli-*` 或兄弟插件 `dreamina-canvas-plugin` 的
  `dreamina-canvas-generate-image` / `download-assets`
- 评估 Dreamina 视频项目 shot 是否过门禁（measured gates）→
  `dreamina-video-evaluator`（**硬门禁，4 选 1 决策**，与本技能**不重叠**）
- 评估 prompt 文本本身 → 走对应 `dreamina-prompt-*` 技能自带的 gotchas 校验

## Core Methodology

打分流程固定为 **4 步**，每步都必须发生：

1. **载入两图**。`target.png`（基准）与 `candidate.png`（被评图）。
   - 通常来自 `.dreamina-canvas-target/target.png` 与
     `.dreamina-canvas-target/latest.png`（见 `dreamina-canvas-harness` §5.1
     工作目录约定）。
   - 也可以直接接收两条绝对路径。
2. **fresh-context subagent 评审**。**关键纪律**：
   - **必须 fresh context**：subagent 不能看到 candidate 的 prompt 草稿、
     模型 id、生成轮次、历史打分；只能看到 target + candidate 两图与 4 维
     rubric。同模型既写 prompt 又打分时，会收敛到 scorer 自身偏好——这是
     dream-loop 已经反复验证的失败模式。
   - **不 fork**：在主线程内起 subagent，不要 fork-with-history。
   - **不查 web**：评分只看两图本身，不做外部检索。
3. **4 维 rubric 打分**。每维详见 `references/rubric.md`：

   | 维度 | 区间 | 关注点 |
   |------|------|--------|
   | Composition | 0–3 | 镜头、构图、主体位置与尺度 |
   | Lighting | 0–3 | 色温、曝光、阴影、反射、大气 |
   | Materials | 0–3 | 表面质感、纹理、半透、湿感 |
   | Details | 0–1 | 高频细节（小颗粒、纹理、刻字、笔触） |

   `total = sum`，满分 10。可给 0.5 步长。
4. **输出评分卡 + 差距清单**。**必须命名具体差异**（"主光方向偏左 30°"），
   不允许"整体偏暗"这种零操作性反馈。

### 输出格式（评分卡 JSON）

```json
{
  "schema_version": "vision_judge/1",
  "rubric": {
    "composition": 0.0,
    "lighting":    0.0,
    "materials":   0.0,
    "details":     0.0,
    "total":       0.0
  },
  "gaps": [
    {
      "dimension": "lighting",
      "severity": "high",
      "location": "前景角色面部右侧",
      "observation": "主光来自右上方，但 target 来自左上方",
      "fix_hint": "调整 key light 至主光左上方 30°，envmap 同步旋转"
    }
  ],
  "summary_one_line": "<一句话结论，给用户的最小可读输出>",
  "judge_context": {
    "model": "<subagent model id>",
    "fresh_context": true,
    "saw_prompt_draft": false
  }
}
```

外加一份 markdown 报告写到
`.dreamina-canvas-target/judges/<UTC-timestamp>.md`（harness §5.3 已规定
存档位置）。**报告不回写 saved draft**——避免污染 dreamina-canvas 的
submitId 幂等性。

## How to use this skill

### Step 1：确认输入

两个图路径 + 可选的 `aspect` / `genre_hint`（如 fantasy / portrait）。
**如果没有图就停**——本技能不调用图像生成。

### Step 2：起 fresh-context subagent

子任务描述里只给：

- target.png 绝对路径
- candidate.png 绝对路径
- aspect（如 `16:9`）/ genre_hint（可选）
- 4 维 rubric 文本（直接内联，或指向 `references/rubric.md`）
- 输出 JSON schema（见上文）

明确禁止给 subagent：

- candidate 的 prompt 文本 / model id / 之前的 rubric 历史
- 任何对"上一版哪里不对"的暗示

### Step 3：解析评分卡

把 subagent 输出解析成 JSON 评分卡（schema 校验见 `references/rubric.md`
末尾）。任一维度 < 0 或 > 上限 → 标记为 `INVALID`，不二次猜测。

### Step 4：写 markdown 报告 + 抛给用户

报告至少包含：4 维分数、总分、`gaps` 表格、按严重度排序的 fix hints。
报告**作为建议**交给用户，**不**自动修改生成参数。

## Gotchas

1. **fresh-context 是硬约束**。同模型既写 prompt 又打分会向 scorer 偏好收敛；
   dream-loop 的 4 维 rubric 没有这条机制就退化成"夸自己"。
2. **必须命名具体差异**。`gaps[].observation` 必须包含位置 + 现象 + 与 target
   的具体差距。空泛反馈（如"颜色不对"）等于无反馈。
3. **总分区间 [0, 10] 之外的分数 = 程序错误**。不要"打分宽松到 12"补回。
4. **细节维度（Details）上限 1**。dream-loop 的 4 维总和 /10 = 3+3+3+1；
   偷工减料把 details 调到 2 会破坏跨技能可比性。
5. **不消耗 dreamina-canvas 积分**。本技能只调用 vision 模型（本地或 LLM
   API）；生成 / quote / confirm / run 走 `dreamina-canvas-*` 兄弟技能。
6. **评分不写回 saved draft**。dreamina-canvas 的 saved draft 有 submitId
   幂等约束；vision-judge 结果单独存档到
   `.dreamina-canvas-target/judges/<timestamp>.md`，由用户决定是否回到
   `dreamina-canvas-generate-image` / `-video` 继续编辑。
7. **aspect 比率不匹配 = 必扣 composition 分**。两张图 aspect 不同（如 16:9
   vs 1:1）就构图先失分，提示用户要么裁切到一致 aspect 要么换目标图。
8. **vision-judge 与 dreamina-video-evaluator 不混用**。前者软 rubric（4 维
   分数 + 差距），后者硬门禁（accepted/retry/rejected/manual_review）。详见
   description 字段里的互斥声明。

## Available Resources

| Resource | Description | When to Load |
|----------|-------------|--------------|
| `references/rubric.md` | 4 维 rubric 详细定义 + 输出 JSON schema 校验规则 | 每次评审前必读 |
| `examples/happy-path.md` | 一次端到端评审 demo（候选 vs 目标） | 首次使用本技能 |
| `examples/boundary-refusal.md` | 输入缺图 / aspect 不匹配时的拒收示例 | 边界场景 |
| `examples/failure-recovery.md` | subagent 输出不可解析时的恢复示例 | 异常路径 |

<!-- QUALITY_BASELINE_V1 -->
## When to use（什么时候使用）

当用户需要 **对一个已有产物做不修改产物的视觉评估** 时加载本技能。先从
请求中提取两图路径、aspect、可选 genre_hint 与交付格式；描述摘要为：
Compare a candidate image against a target image using a fresh-context vision
subagent and a 4-dimension rubric (composition / lighting / materials / details,
total /10). Use when the user asks for visual evaluation against a target image,
wants to score image fidelity, or wants a structured gap list to drive the next
iteration of an image-generation prompt。。

## Rules

- 先读后写：先确认两图都存在且可读，再启动 subagent。
- 权限最小化：subagent 只读两图，不读 prompt / model id / 历史 rubric。
- 证据优先：评分卡 JSON 与 markdown 报告落 `.dreamina-canvas-target/judges/`。
- 幂等优先：评分独立存档，不写回 saved draft；同一对图多次评审由调用方
  决定是否覆盖。
- 隐私安全：日志、报告、评分卡不写 prompt 草稿原文，避免无意泄露未发布
  创意。

## Workflow

### Step 1：澄清意图

确认本技能是否匹配目标；若只是相邻需求（写 prompt / 执行生成 / 视频项目
门禁），交给更精确的技能。

### Step 2：执行预检

两图路径都解析为 `.jpg` / `.png` / `.webp`，任一不存在即停；aspect 不一致
先提示用户。

### Step 3：形成计划

列出 subagent 任务描述（仅含 target + candidate + rubric）、期望输出 schema
、报告落盘路径。

### Step 4：执行动作

起 fresh-context subagent，喂两图路径与 rubric，等待返回。解析 JSON 校验。

### Step 5：验证交付

校验评分卡 schema、写 markdown 报告、抛给用户作为建议。

## Validation checklist

- [ ] subagent 上下文确为 fresh（无 prompt / 历史 rubric 输入）。
- [ ] 4 维分数在合法区间，total = sum 且 ∈ [0, 10]。
- [ ] `gaps[]` 每条都含 location / observation / fix_hint。
- [ ] markdown 报告已落 `.dreamina-canvas-target/judges/<timestamp>.md`。
- [ ] 未修改任何 saved draft / 未调用 dreamina-canvas --run。

## Gotchas

1. **把计划当结果**：评分卡不是真实改图；必须标明它只是建议。
2. **错误重试**：subagent 输出 JSON 解析失败时，不二次猜测打分，重新起
   subagent 并明示约束。
3. **隐式扩大范围**：本技能不调用 dreamina-canvas 角色侧；想做"评分 + 立即
   重生成"必须分两步，跨技能交接给 `dreamina-canvas-generate-image`。
4. **版本漂移**：rubric 版本由 schema_version 字段标识；下游消费者不要写
   死 `vision_judge/1`，要按字段解析。
5. **证据过期**：存档里的评分卡与当时的图像绑定；图像被覆盖或重命名后，
   评分卡不可信——下游若引用请先校验 path 仍指向同字节内容。

## 不适用与边界

vision-judge 是软评估，不替代用户的最终验收。skip 路径：用户已经满意 / 两图
不匹配 aspect / 任务是改图而非评审。 如果请求需要别的技能，不复制其正文；
按技能名称进行交接，并保留当前任务上下文。

## Progressive disclosure

- 需要 4 维 rubric 详细定义、JSON schema 与 subagent 任务模板时，读取
  `references/rubric.md`（每次评审前必读）。
- 首次使用本技能看完整端到端 demo：读取 `examples/happy-path.md`。
- 输入缺图 / aspect 不匹配 / 输入是视频等拒收场景：读取
  `examples/boundary-refusal.md`。
- subagent 输出超界 / 含 prose / saw_prompt_draft = true 等解析失败：读取
  `examples/failure-recovery.md`。