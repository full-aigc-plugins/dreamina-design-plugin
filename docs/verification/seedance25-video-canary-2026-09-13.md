# Seedance 2.5 Video Canary — 2026-09-13

## Authorization

The user explicitly authorized completion of the Blender and Seedance path
verification in the controlling Codex task. The agent warned that generation
would consume credits and submitted exactly one minimum-duration,
minimum-resolution Seedance 2.5 task.

## Input

- source: Blender 5.2.1 LTS preview-only adapter
- format: H.264 MP4
- dimensions: 320 × 180
- fps: 24
- duration: 2 seconds
- SHA-256: `2a38c024311534e8ddcced9927a9c4fe64a5c05cba91cd5e7879712ebced8e6e`

## Submission

- command mode: `multimodal2video`
- model: `seedance2.5`
- output resolution: `480p`
- requested duration: 4 seconds
- submit ID: `9f703ef1-3cf2-452a-bfde-4c96433e4434`
- terminal state: `success`
- task-attributed credit count: 54
- resubmissions: 0

The overall account balance also changed because another 192-credit task was
running concurrently. The balance delta is therefore not used as canary cost
evidence; the task record's `commerce_info.credit_count` is used.

## Verified Artifact

- format: H.264/AAC MP4
- dimensions: 854 × 480
- measured fps: `5820/241` (approximately 24.15)
- CLI-reported fps: 24
- ffprobe duration: 4.063991 seconds
- bytes: 762,550
- SHA-256: `950e9a25bd773c04aace0bd0ca5e2b72a255f7abf69286c8677637b6b4f79a75`

The result was accepted only after terminal success, download, ffprobe, file
size inspection, and independent SHA-256 verification.
