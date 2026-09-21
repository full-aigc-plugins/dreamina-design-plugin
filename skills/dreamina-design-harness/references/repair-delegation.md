# 修复轮次的委托纪律（worker / 编排者 / 评审者）

当视觉质量闭环的修复实现被委托给 worker 子代理时，必须遵守以下约束。
它们存在的唯一目的：让独立评审保持有效——实现者不得充当自己的验收者。

## 1. worker 必须全新空上下文

- worker 以**全新空上下文**启动，绝不 fork、绝不继承编排者或此前轮次的历史；
- 委托材料只含本轮显式提供的：目标素材、当前产物、用户诉求；
- 不附带历史对话、历史评分或编排者的私密推理。

## 2. 委托指令只描述高层目标

给 worker 的指令 = 目标 + 当前状态 + 用户诉求，仅此三样：

- ❌ 不给逐条修复任务清单（编排者通常弱于被委托者，逐条派发会把最终质量
  锚定在弱方的判断上）；
- ❌ 不把上一轮评审的差距清单当作任务单派发；
- ✅ worker 自行判断如何收敛差距。

## 3. 验证职责属于编排者

- 委托时明确告知 worker：**只实现，不验证**；
- worker 返回后，编排者自行做可运行性检查（能否加载、方向是否正确、
  装配是否完好），并修正这类**非视觉**缺陷——但不借这一步调整视觉方向；
- 视觉是否达标由独立评审判定，不由编排者或 worker 自行宣布。

## 4. worker 不得自行发起循环

- 委托内容明确禁止 worker 启动/推进视觉循环、禁止 worker 提交任何付费生成；
- 循环的发起、轮次推进与付费决策只由编排者在既有门禁下进行；
- 同一轮委托内不产生额外付费提交。

## 5. 实现与评审必须角色分离

- 同一轮内，实现者与评审者**不得**是同一上下文；
- 评审走 `dreamina-vision-judge` 的 fresh-context 子代理，其输入不含本轮
  实现过程与编排者的主观意见；
- 实现者对自己的产物出具的任何"看起来达标"结论，不作为有效验收证据。

## 委托指令模板

```
You are implementing one repair round toward a fixed target.

Target image: <absolute path>
Current state: <absolute path or description>
User goal: <one line, verbatim from the user>

Close the gap between the current state and the target across composition,
layout, lighting, materials, and fine details. You decide how.

Rules:
- Implement only. Do NOT run acceptance checks; the orchestrator verifies.
- Do NOT start or advance any visual loop; do NOT submit any paid generation.
- Do not ask for the previous rounds' scores or feedback; none are provided.
```
