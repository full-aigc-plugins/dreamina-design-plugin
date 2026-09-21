# Happy Path — 一次端到端视觉评审

本示例演示 `dreamina-prompt-vision-judge` 的完整执行链路。

## 场景

用户在使用 `dreamina-canvas-plugin` 生成一张 fantasy portrait，已经迭代
过两轮。`.dreamina-canvas-target/` 工作目录已建立：

```
.dreamina-canvas-target/
  target.png         # 用户上传的参考图（暖色侧光，皮革披风，金属臂铠）
  captures/
    2026-09-21T10-00-12.png  # 第一轮生成
    2026-09-21T11-30-45.png  # 第二轮生成
  latest.png -> captures/2026-09-21T11-30-45.png
  judges/
    2026-09-21T10-15-30.md   # 第一轮评审报告（已存在）
```

## 调用

```text
用 dreamina-prompt-vision-judge 评审：
target.png = .dreamina-canvas-target/target.png
candidate.png = .dreamina-canvas-target/latest.png
aspect = 4:5 / 4:5 (一致)
genre_hint = fantasy portrait
```

## subagent 任务（实际发出的内容）

只含两图路径 + aspect + genre_hint + 4 维 rubric + JSON schema。
**不**含 candidate 的 prompt / model id / 历史 rubric。

## subagent 返回（评分卡 JSON）

```json
{
  "schema_version": "vision_judge/1",
  "rubric": {
    "composition": 2.5,
    "lighting":    2.0,
    "materials":   2.5,
    "details":     0.5,
    "total":       7.5
  },
  "gaps": [
    {
      "dimension": "lighting",
      "severity": "high",
      "location": "面部右侧高光",
      "observation": "candidate 的主光从右上来；target 的主光从左上来；色温接近但方向相反",
      "fix_hint": "在 prompt 加 'key light from upper-left at 30°'，并旋转 envmap 主光源方向"
    },
    {
      "dimension": "materials",
      "severity": "medium",
      "location": "金属臂铠",
      "observation": "candidate 表面有 envmap 反射但缺少环境光斑驳；target 有清晰的环境污渍/划痕",
      "fix_hint": "补 normal map 与粗糙度变化；或后处理加 scratch pass"
    },
    {
      "dimension": "details",
      "severity": "low",
      "location": "披风边缘",
      "observation": "candidate 边缘过于干净；target 有可见纤维与灰尘",
      "fix_hint": "加 post-processing vignette grain + edge fibers；或 prompt 加 'visible fabric fibers, dust motes'"
    }
  ],
  "summary_one_line": "总分 7.5；构图接近，主光方向左右相反是最大差距，金属与披风细节建议补 normal map 与纤维。",
  "judge_context": {
    "model": "gpt-6-astra",
    "fresh_context": true,
    "saw_prompt_draft": false
  }
}
```

## markdown 报告（自动落盘）

写入 `.dreamina-canvas-target/judges/<UTC-timestamp>.md`：

```markdown
# Vision Judge 报告 — 2026-09-21T11:45:02Z

## 评分卡

| 维度 | 分数 |
|------|------|
| Composition | 2.5 / 3 |
| Lighting    | 2.0 / 3 |
| Materials   | 2.5 / 3 |
| Details     | 0.5 / 1 |
| **Total**   | **7.5 / 10** |

## 差距清单（按严重度）

### high

- **lighting / 面部右侧高光**：candidate 主光来自右上方；target 来自左上方。
  修法：在 prompt 加 `key light from upper-left at 30°`，旋转 envmap 主光源方向。

### medium

- **materials / 金属臂铠**：envmap 反射存在但缺环境污渍/划痕。
  修法：补 normal map 与粗糙度变化；或后处理加 scratch pass。

### low

- **details / 披风边缘**：边缘过干净，缺纤维与灰尘。
  修法：post-processing vignette grain + edge fibers；或 prompt 加
  `visible fabric fibers, dust motes`。

## 一句话结论

构图接近，主光方向左右相反是最大差距；金属与披风细节建议补 normal map 与
纤维。

## Judge 上下文

- 模型：`gpt-6-astra`
- Fresh context：是
- 看过 prompt 草稿：否
```

## 下一步建议（不动 saved draft）

把"主光方向"的修法写到下一轮 `dreamina-canvas-generate-image` 的 prompt
里；同时**不要**回写到本轮的 saved draft——保持 submitId 幂等性。

## 验收 checklist

- [x] subagent fresh context（`judge_context.fresh_context === true`）
- [x] 4 维分数在合法区间
- [x] `gaps[]` 每条都有 location / observation / fix_hint
- [x] markdown 报告已落 `.dreamina-canvas-target/judges/`
- [x] 未修改任何 saved draft，未调用 `dreamina-canvas --run`