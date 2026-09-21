---
description: 用目标图迭代图像产物：锁定目标 → 付费生成 → 宿主独立评审 → 受额度约束的重试。
argument-hint: "[目标图或目标描述]"
skills: dreamina-design-harness
---

Use the `dreamina-design-harness` skill for this request:

$ARGUMENTS

Drive one visual-quality loop with the `dreamina_visual_loop` MCP tool:

1. `create` with the media kind, then `lock_target` with the target image and its
   approved roots. If the user has no target image, ask before generating one —
   target generation is a paid submission.
2. `run_first_round` reserves the round and runs one approved paid generation. It
   returns the artifact plus `judge_evidence`.
3. Hand `judge_evidence` to `dreamina-vision-judge` (a fresh-context subagent) and
   pass its structured result back through `record_judgement`.
4. A failed round never resubmits by itself. A retry needs `propose_retry` then an
   exact-fingerprint, credit-ceiling-bound `approve_retry`.
5. Report the loop state, the real `submit_id`, the verified artifact digest and
   what remains unverified. Use `stop` to end the loop; it does not cancel anything
   remotely.
