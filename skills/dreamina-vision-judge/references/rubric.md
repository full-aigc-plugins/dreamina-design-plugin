# 4 维 Rubric 详细定义

本文件是 `dreamina-vision-judge` 的 4 维评分细则。每个 subagent 在
评审前**必须**先读它；不读即视为违规。

## 4 维定义

### 1. Composition（0–3）

镜头、构图、主体位置与尺度。

| 分数 | 含义 |
|------|------|
| 0 | 镜头/构图完全错位（如正面特写 vs 全景）；主体位置/尺度与 target 差异巨大 |
| 1 | 镜头类型对，但主体位置/尺度明显错位；或构图能辨认但与 target 视觉重心相反 |
| 2 | 镜头接近，但有 1–2 处明显错位（如主体偏左 30%、前景道具缺失） |
| 3 | 镜头、构图、主体位置/尺度均与 target 视觉一致 |

**常见扣分点**：
- aspect 比率不匹配（target 16:9 vs candidate 1:1）→ 一律扣到 ≤ 1
- 主体偏出画面边缘或占比 < target 一半 → 扣 1
- 前景/背景纵深关系倒置（target 前清后浊，candidate 前浊后清）→ 扣 1

### 2. Lighting（0–3）

色温、曝光、阴影、反射、大气。

| 分数 | 含义 |
|------|------|
| 0 | 光照方向/色温完全错；candidate 是日景 target 是夜景或反之 |
| 1 | 光照类型对，色温/曝光有 ≥ 2 stops 偏差；氛围（warm/cool/moody）相反 |
| 2 | 色温/曝光接近，但主光方向或大气层有 1 处明显错；反射缺失或错位 |
| 3 | 色温、曝光、阴影、反射、大气与 target 视觉一致 |

**常见扣分点**：
- 主光方向左右相反 → 扣 1（最常见错误：subagent 用"北半球默认光"硬猜）
- 高光区/阴影区比例倒置 → 扣 1
- 反射面无环境反射（target 有，candidate 死黑）→ 扣 0.5
- 整体过曝/欠曝 ≥ 2 stops → 扣 0.5–1

### 3. Materials（0–3）

表面质感、纹理、半透、湿感。

| 分数 | 含义 |
|------|------|
| 0 | 材质类型完全错（target 是湿石，candidate 是塑料；target 是丝绸，candidate 是帆布） |
| 1 | 材质类型对，但质感参数全错（blocky / plasticky / smooth / fake，且 target 不如此） |
| 2 | 材质方向对，1–2 处细节缺失或过度（如缺 normal map、缺湿感、缺颗粒） |
| 3 | 材质类型与质感参数均与 target 一致 |

**常见扣分点**：
- 金属表面像塑料（无 envmap 反射）→ 扣 1
- 布料缺褶皱或纤维纹理 → 扣 0.5
- 皮肤毛孔/雀斑/细纹缺失（target 有）→ 扣 0.5
- 玻璃/水面无半透或折射 → 扣 1

### 4. Details（0–1）

高频细节（小颗粒、纹理、刻字、笔触、灰尘、划痕）。

| 分数 | 含义 |
|------|------|
| 0 | 整体看起来与 target 不在同一时代/技术层次（target 有大量细节，candidate 一片干净） |
| 1 | 高频细节与 target 接近；不要求像素级一致，但"质感密度"对齐 |

**重要**：Details 维度上限为 1，这是为了保持跨技能可比性；不要给 2 来"补偿"
其他维度扣分。

## 总分

`total = composition + lighting + materials + details`，满分 10。

| total | 含义 |
|-------|------|
| 0–3 | 候选与目标差距极大；建议重写 prompt 或换模型 |
| 4–6 | 有可见相似但差距多；差距清单是下一轮迭代输入 |
| 7–8 | 接近目标；建议针对高分 gap 修一轮 |
| 9–10 | 视觉一致；dream-loop 默认 ≥ 8 退出循环 |

## 评分卡 JSON Schema（强约束）

下游消费者按字段解析，**不要**用正则匹配 message。

```json
{
  "type": "object",
  "required": ["schema_version", "rubric", "gaps", "summary_one_line", "judge_context"],
  "properties": {
    "schema_version": { "const": "vision_judge/1" },
    "rubric": {
      "type": "object",
      "required": ["composition", "lighting", "materials", "details", "total"],
      "properties": {
        "composition": { "type": "number", "minimum": 0, "maximum": 3 },
        "lighting":    { "type": "number", "minimum": 0, "maximum": 3 },
        "materials":   { "type": "number", "minimum": 0, "maximum": 3 },
        "details":     { "type": "number", "minimum": 0, "maximum": 1 },
        "total":       { "type": "number", "minimum": 0, "maximum": 10 }
      }
    },
    "gaps": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["dimension", "severity", "location", "observation", "fix_hint"],
        "properties": {
          "dimension": { "enum": ["composition", "lighting", "materials", "details"] },
          "severity":  { "enum": ["low", "medium", "high"] },
          "location":  { "type": "string" },
          "observation": { "type": "string" },
          "fix_hint":  { "type": "string" }
        }
      }
    },
    "summary_one_line": { "type": "string", "maxLength": 200 },
    "judge_context": {
      "type": "object",
      "required": ["model", "fresh_context", "saw_prompt_draft"],
      "properties": {
        "model": { "type": "string" },
        "fresh_context": { "const": true },
        "saw_prompt_draft": { "const": false }
      }
    }
  }
}
```

## subagent 任务模板

subagent 输入**只能**是这两段拼起来，**禁止**附加 prompt 草稿或历史 rubric：

```
You will judge visual fidelity of candidate.png against target.png.

Target image: <absolute path>
Candidate image: <absolute path>
Aspect (target / candidate): <e.g. 16:9 / 16:9>
Optional genre hint: <e.g. fantasy portrait>

Score along this rubric (each dimension's detail in references/rubric.md):
- Composition (0-3): camera, framing, layout, subject placement & scale
- Lighting (0-3): color temperature, exposure, shadows, reflections, atmosphere
- Materials (0-3): surface qualities, texture, translucency, wetness
- Details (0-1): high-frequency detail density (grain, scratches, micro-texture)

You may give 0.5 increments. total = sum (0-10).

For each gap, you MUST name:
- the location in the image
- the specific difference
- an actionable fix hint (e.g. "rotate key light 30° clockwise")

Avoid vague feedback like "this looks fake". Name the exact pixel-level or
structural reason and how to fix it.

Output STRICT JSON matching the schema below. No prose outside the JSON.
<schema>
{paste the JSON schema above}
</schema>
```

## subagent 上下文卫生

- subagent **不能**接收：candidate 的 prompt 文本、model id、历史 rubric、
  任何"上次哪里不对"的暗示。
- subagent **能**接收：genre_hint、aspect、style keyword（如"noir"）等
  客观参考信息——这些是图像本身的元信息，不构成偏置。
- 上游 caller 必须验证 `judge_context.fresh_context === true` 且
  `saw_prompt_draft === false`，否则评分卡标记为 INVALID 并重做。