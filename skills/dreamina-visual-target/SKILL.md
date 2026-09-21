---
name: dreamina-visual-target
description: Obtain the target image for a visual-quality loop when the user has not supplied one — generate it with the approved paid image tool, or baseline-refine from an existing artifact so the target converges instead of diverging. Use when the user asks to "iterate to a target", has no reference image, says "帮我定个目标图", "生成目标图", "对标图", "target image", "goal image", or when a visual loop needs a target but `lock_target` has nothing to lock. Enforces the anti-concept-art discipline (the target is a screenshot-exact rendering to compare against, NOT a stylized interpretation) and fail-closed behavior without paid-generation approval. NEVER generates without the user's explicit approval — target generation is a paid submission. Do not use for judging (that is `dreamina-vision-judge`).
license: Complete terms in LICENSE.txt
---

# dreamina-visual-target — 目标素材获取

为视觉质量循环取得**目标素材**。目标是循环的对标锚点：评审按它与候选图比对，
目标的质量直接决定迭代上限。本技能只负责"把一个合格的目标锁定进循环"，
不负责评审（评审是 `dreamina-vision-judge`），也不负责执行生成轮次。

## 三条获取路径

按顺序判断，一次只走一条：

### 路径 A：用户已提供目标

用户给了目标图 → 直接校验后进入 `lock_target`，**不要**再生成。
这是唯一零付费的路径。

### 路径 B：已有产物，缺目标（基线精修）

用户已有一个产物或早期版本，但没给目标：

1. 取得当前产物的截图或文件作为**输入**；
2. 用图像生成（`dreamina_submit_image`，付费）以当前产物为输入生成**改进版**目标；
3. 目标必须保留原产物的主体与构图方向——是**收敛**，不是另一个方向。

跳过基线精修直接生成全新目标，会让目标与现有产物在主体、构图上漂移，
评审的四个维度同时大幅偏离，迭代难度反而更高。

### 路径 C：全新创作，无目标

不存在任何现有产物 → 直接用图像生成（付费）产出全新目标，
不需要先造一个基线。

## 反演绎纪律（必须遵守）

目标是**可逐项对照的成品呈现**，不是风格化诠释：

- ❌ 反例："一张概念艺术风格的奇幻城堡，氛围宏大" —— 这是艺术演绎，
  评审无法用它逐像素或逐区域比对；
- ✅ 正例："该城堡场景在最终引擎内的实拍截图：正面机位、主体居中、
  石材质感清晰、黄昏侧光" —— 构图、光照、材质、细节四个维度都可对照。

生成意图描述中避免把结果推向演绎风格的表述。纪律约束的是**意图类别**
（可对照成品 vs 风格化诠释），不维护禁用词表。

## 目标质量自检（锁定前）

- [ ] 目标与候选处于**同一呈现类别**（都是成品呈现，不是一个概念图对一个成品）
- [ ] 画幅可比（aspect 一致或接近，评审的构图维度才能比对）
- [ ] 逐项可对照：能指着目标的某个区域说出"这里应该是 X"

## 付费门禁（不可绕）

目标生成是一次**付费提交**，与 `dreamina-design-harness` §2 同一门禁：

- 必须先获得用户对该次生成的明确授权；
- 无授权、审批被拒、图像生成能力不可用 → **停下询问**，
  不静默降级为"无目标迭代"，不借用其他通道；
- 不自动重试。

## 来源记录（锁定时）

`lock_target` 的 `source` 字段必须如实记录：

```json
{"kind": "user_supplied"}
{"kind": "generated", "provider": "dreamina", "submit_id": "<真实提交标识>"}
{"kind": "existing_artifact"}
```

生成类目标必须携带真实 `submit_id`，使目标可审计。目标一经锁定不可被静默替换；
换目标 = 显式创建新目标版本。

## 与其他技能的衔接

- 拿到目标后 → `dreamina_visual_loop` 的 `lock_target` 锁定，再走
  `run_first_round` → `dreamina-vision-judge` 评审 → `record_judgement`；
- 评审结论如何交给修复实现 → 见 harness 的 `references/repair-delegation.md`。
