# 宿主评审适配配方（JudgePort 的实现方法）

闭环的一轮由两个调用组成：`run_first_round` 拿到 `judge_evidence`，评审后用
`record_judgement` 提交结论。本文件是宿主（即正在读这段的你）完成中间那步
**评审**的可操作配方。契约细节见插件文档 `docs/judge-port-adapter-contract.md`；
评分细则见 `dreamina-vision-judge` 技能的 `references/rubric.md`。

## 步骤

1. **起全新子代理**：用宿主的子代理机制（ZCode 的 Agent 工具、Codex 的
   子代理等）创建一个**空上下文**的评审者。不 fork 当前会话，不附带任何历史、
   提示词草稿、模型标识或此前评分。
2. **注入最小材料**：只有三样——`judge_evidence.target` 的图、
   `judge_evidence.artifact` 的图、下面的评审提示（含 rubric 摘要）。
3. **要求严格 JSON**：子代理只输出一个 JSON 对象，无任何额外文字。
4. **宿主侧校验后再提交**：确认七个字段齐备、`score ∈ [0,10]`、
   `gates.fresh_context === true`、`gates.saw_prompt_draft === false`；
   任一不满足 → 丢弃结论，重起子代理（最多重试一次），仍失败则停下询问。
5. **提交**：调用 `dreamina_visual_loop`，`action: record_judgement`，
   附上原样返回的 `artifact` 与校验后的 `judge_result`。

## 子代理评审提示（模板）

```
You will judge visual fidelity of the CANDIDATE image against the TARGET image.

Target image: <target 路径>
Candidate image: <artifact 路径>

Score along this rubric:
- Composition (0-3): camera, framing, layout, subject placement & scale
- Lighting (0-3): color temperature, exposure, shadows, reflections, atmosphere
- Materials (0-3): surface qualities, texture, translucency, wetness
- Details (0-1): high-frequency detail density (grain, scratches, micro-texture)

Fractional scores (0.5 steps) allowed. total = sum, 0-10. Be nitpicky:
for every gap name the location, the specific difference, and an actionable
fix hint. Avoid vague feedback like "this looks fake".

Output STRICT JSON only, matching:
{"provider":"<you>","model":"<you>","evaluated_at":"<utc>","rubric_version":"vision_judge/1",
 "score":<total>,"gates":{"fresh_context":true,"saw_prompt_draft":false},
 "blocking_gaps":["<location>: <observation> -> <fix_hint>", ...]}
```

## 与委托纪律的关系

- 评审者**不是**修复实现者：实现走 worker（见
  `references/repair-delegation.md`），评审永远独立；
- 同一轮内不要让同一个子代理既实现又评审；
- 评审结论不可伪造"看起来达标"——`record_judgement` 的失败关闭校验会拒绝
  结构不合规的结论，而语义造假只会让循环停滞，最终由停滞检测暴露。
