# JudgePort 宿主适配器契约

> 版本：0.6.0 · 状态：契约已冻结（与 `scripts/visual_quality_loop.py` 的领域校验一一对应）

`JudgePort` 是视觉质量闭环的评审边界。**领域层从不调用任何视觉模型**——
生产服务是 stdio MCP 进程，无法在自身进程内启动 fresh-context 子代理；
评审由**宿主**（Codex / Claude Code / ZCode / Kimi 等）提供。本文件是宿主
实现该端口时必须满足的闭合契约。

## 输入：宿主收到什么

`dreamina_visual_loop` 工具的 `run_first_round` / `run_retry` 返回
`judge_evidence`：

```json
{
  "target":   { "source_path": "...", "sha256": "...", "width": 0, "height": 0, "...": "..." },
  "artifact": { "submit_id": "...", "path": "...", "sha256": "...", "size_bytes": 0, "mime_type": "image/png" },
  "previous_round": null
}
```

宿主把 `target.source_path` 与 `artifact.path` 两张图交给评审。

## 输出：宿主必须返回的闭合结构

`record_judgement` 的 `judge_result` 必须是**恰好七个字段**的对象
（多一字段、少一字段都会被领域层拒绝）：

| 字段 | 类型 | 约束 |
|---|---|---|
| `provider` | string | 评审方身份（如 `zcode`） |
| `model` | string | 评审模型标识 |
| `evaluated_at` | string | UTC 时间戳 |
| `rubric_version` | string | 提示规范版本（当前 `vision_judge/1`） |
| `score` | number | **0–10**；越界即失败关闭 |
| `gates` | object | 评审的自证门（如 `fresh_context` / `saw_prompt_draft`） |
| `blocking_gaps` | string[] | 阻塞差距清单，每条须 location + observation + fix_hint |

领域层额外执行：规范 JSON 校验、评分越界拒绝、产物摘要复核。
**分数语义**：`score ≥ 8.0` 且无阻塞差距 → 完成；差距与上一轮重复或
提升不足 1 分 → 重规划；其余 → 等待授权重试（至多一次，受精确指纹 + 额度约束）。

## 上下文卫生（不可协商）

- 评审者必须是**全新空上下文**的独立子代理——不 fork、不带历史、不带此前评分；
- 子代理**不得**看到候选图的提示词草稿、模型标识或"上次哪里不对"的暗示；
- `gates.fresh_context` 必须为 `true`、`gates.saw_prompt_draft` 必须为 `false`；
  伪造该自证 = 评审结论无效；
- 实现者不得评审自己的产物（角色分离，见 harness `references/repair-delegation.md`）。

## 失败语义

- 子代理输出不是严格 JSON / 字段缺失 / 评分越界 → 本次评审作废，**重起子代理**，
  而不是降级放行；
- 评审无法进行（图缺失、画幅不可比）→ 用 `stop` 结束循环并如实报告，
  不构造占位结论。

## 评分方法

四维 rubric（composition 0–3 / lighting 0–3 / materials 0–3 / details 0–1，
允许 0.5 步进）与子代理任务模板见
`skills/dreamina-vision-judge/references/rubric.md`；可操作的宿主适配配方见
`skills/dreamina-design-harness/references/judge-port-adapter.md`。
