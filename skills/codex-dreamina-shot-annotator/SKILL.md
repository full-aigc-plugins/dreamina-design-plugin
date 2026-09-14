---
name: codex-dreamina-shot-annotator
description: Use when labelling contact sheets or keyframes with shot semantics before a Dreamina redesign, producing shot_annotation.schema.json content only.
---

# Dreamina Shot Annotator

Annotate only. This Skill produces `shot_annotation.schema.json` content and
nothing else.

## 能力边界说明

### ✅ 能做

- Read contact sheets and keyframes produced by the analysis step.
- Describe each shot's subject, action, camera behaviour, continuity, and preservation constraints.
- Emit annotations that match `shot_annotation.schema.json` exactly.

### ⚠ 需要素材

- A completed machine analysis version and its contact-sheet artifacts.
- The caller's machine fingerprint, so the annotation can be bound to it.

### ❌ 超出范围

- Do not produce `shot_evaluation.schema.json`. Accept-or-reject decisions belong to `codex-dreamina-video-evaluator`.
- Do not edit measured timestamps, motion, media identity, or boundary provenance.
- Do not assert rights.

## Workflow

1. Receive the contact sheets and the machine analysis version.
2. Describe each shot using only the closed fields in `shot_annotation.schema.json`.
3. Return the annotations with the machine fingerprint it was written against.

## Never do

- Never write measured values; those come from the machine analysis.
- Never emit an evaluation verdict.
- Never widen the annotation schema.
