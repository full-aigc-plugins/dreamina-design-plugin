# Boundary Refusal — 输入缺图 / aspect 不匹配

本示例演示 `dreamina-vision-judge` 何时应该**拒收**而不是强行评审。

## 场景 A：缺图

```text
用 dreamina-vision-judge 评审：
target.png = .dreamina-canvas-target/target.png
candidate.png = /tmp/missing.png   # 文件不存在
```

正确拒收：

```text
本技能已停止：

- 候选图 `/tmp/missing.png` 不存在。
- 本技能是纯评估，不调用图像生成；不会"先生成再评"。

修复方式：
1. 重新提供 candidate.png 的绝对路径；
2. 或先调用 `dreamina-canvas-generate-image` / `-video` 与
   `dreamina-canvas-download-assets` 拿到产物再评审。
```

**禁止**的回应方式：
- ❌ "我先用 fig 评价一张空白图" → 跳过 subagent 直接打分
- ❌ "我帮你先生成一张再评" → 超出本技能职责
- ❌ "我先看看是不是文件名打错了" → 跨过拒收

## 场景 B：aspect 不匹配

```text
用 dreamina-vision-judge 评审：
target.png = .dreamina-canvas-target/target.png   # 16:9
candidate.png = .dreamina-canvas-target/latest.png # 1:1
```

正确拒收：

```text
本技能已停止：

- target.png 是 16:9（宽 1600 / 高 900），candidate.png 是 1:1（宽 1024 / 高 1024）。
- aspect 不一致时，构图（Composition）维度必然失分；用户对最终产物的视觉
  期望与 target 已经不在同一画布尺寸下。
- 本技能不做自动裁切或 pad，因为那是上游生成阶段的选择。

修复方式：
1. 用户确认 target vs candidate 真的要在不同 aspect 下对比 → 我把 Composition
   上限硬扣到 1，并在报告中显式说明；
2. 用户接受裁切 candidate 到 16:9（或 pad target）→ 重新走
   `dreamina-canvas-generate-image` 出图，再调用本技能。
```

**禁止**的回应方式：
- ❌ "我帮您把 candidate 裁到 16:9 再评" → 副作用，且越权
- ❌ "aspect 不一致也没关系" → 纵容视觉失真

## 场景 C：两图之一是视频帧

```text
用 dreamina-vision-judge 评审视频：
target.mp4 = .dreamina-canvas-target/target.mp4
candidate.png = .dreamina-canvas-target/latest.png
```

正确拒收：

```text
本技能已停止：

- 输入是视频文件，本技能只评审两张静态图。
- 视频评估（动态镜头、连贯性、镜头间一致性）→ `dreamina-video-evaluator`
  或 `dreamina-prompt-text2video` / `-image2video`。

修复方式：
1. 从 video 抽帧（ffmpeg -ss ... -frames:v 1）后再调用本技能；
2. 或换 `dreamina-video-evaluator`（适用于 Dreamina 视频项目硬门禁）。
```

## 拒收响应模板（统一）

```text
本技能已停止：<一句话原因>

修复方式：
1. <option 1>
2. <option 2>

不要 <禁止行为 1>。
不要 <禁止行为 2>。
```