# Failure Recovery — subagent 输出不可解析时

本示例演示 `dreamina-prompt-vision-judge` 在 subagent 返回值违反 schema 时
的恢复路径。

## 场景 A：subagent 返回 prose + JSON 混杂

subagent 实际返回：

```text
Here is my assessment:

{
  "schema_version": "vision_judge/1",
  "rubric": { ... },
  ...
}

I hope this helps!
```

恢复：

```text
解析失败：subagent 返回包含 prose，违反 "STRICT JSON matching the schema"
约束。已丢弃结果。

恢复方式：
- 重新起 fresh-context subagent，并把任务描述的"STRICT JSON matching the
  schema below. No prose outside the JSON."加重——明示 prose 会被丢弃。
- 不要"先取 JSON 再用 prose 补全分数"，那是把偏置带回来。
```

## 场景 B：rubric 维度超界

subagent 返回 `details: 2`（details 上限 1）。

恢复：

```text
解析失败：rubric.details = 2 超出上限 1。

恢复方式：
- 这是 subagent 对"Details 上限 1"约定不清楚——重新起 subagent 并在任务
  描述里把 rubric 表格内联（不外链），并在文末加一句
  "Details MUST be in [0, 1]; do not give 2 to compensate for other dimensions"。
- 不要"接受 2 然后归一化"，那是改了 dream-loop 的契约，破坏跨技能可比性。
```

## 场景 C：judge_context 卫生失败

subagent 返回 `"saw_prompt_draft": true`（看到了 candidate 的 prompt）。

恢复：

```text
评分卡 INVALID：judge_context.saw_prompt_draft = true，违反 fresh-context
硬约束。

恢复方式：
- 重新起 subagent，**绝对不**附加 candidate 的 prompt 文本 / model id /
  历史 rubric。
- 如果上游 caller 在 subagent 任务里已经塞了这些字段，从调用栈回溯到
  上游 caller，移除之。
- 不要"忽略这条警告继续用分数"，self-scoring 偏置就是 dream-loop 反复
  警告的失败模式。
```

## 场景 D：subagent 完全失联（超时 / 503）

subagent 调用在 vision 模型处超时或返回 503。

恢复：

```text
本轮评审失败：subagent 调用超时 / 503，未拿到评分卡。

恢复方式：
- 不要重试同一 task 描述。改为：
  - 把任务里 vision 模型实例降级（如果有备用实例）；
  - 或把任务拆成两半（先评 Composition + Lighting，再评 Materials + Details）
    让每次调用更短；
- 把这次失败写入 `.dreamina-canvas-target/judges/<timestamp>-FAILED.md`
  便于排查，**不要**写占位符分数（"4 / 4 / 4 / 0 = 12" 这种）。
```

## 场景 E：gaps[] 为空但 total ≥ 8

subagent 返回 total = 8.5 但 `gaps: []`。

恢复：

```text
解析失败：total = 8.5 但 gaps 为空，违反"必须命名具体差异"硬约束。

即使分数高，没有 gaps 也意味着下次迭代没有 actionable 输入。

恢复方式：
- 重新起 subagent 并在任务描述里加：
  "Even at total >= 8, you MUST list at least 3 micro-gaps (severity: low).
  No gap list = invalid output."
- 不要"接受 8.5 当默认通过"，dream-loop 的 stall 退出准则就是依赖 gaps
  命名来分辨"真的接近"与"subagent 偷懒给高分"。
```

## 通用恢复纪律

- **不二次猜测**——subagent 输出错了就是错，重做。
- **不写占位符分数**——失败时落 `*-FAILED.md`，不要"先填 0 / 0 / 0 / 0 占位"。
- **不污染 saved draft**——所有失败报告在 `.dreamina-canvas-target/judges/`
  下，与 dreamina-canvas 的 submitId 状态完全隔离。