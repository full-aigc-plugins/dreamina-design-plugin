# Task 10 Report: Bound Shot Evaluation and Prequoted Retry Selection

## Completion

- Added a closed `shot_evaluation` schema and valid fixture with exact project, batch, shot, attempt, artifact, design, quote, and allowance bindings.
- Added eight trusted-media gates: artifact integrity, dimensions, codec, duration, aspect ratio, frame readability, start anchor, and end anchor.
- Added eight model-only semantic gates: intent, composition, identity continuity, camera behavior, rhythm function, temporal defects, source copying, and subtitle safe area. Unknown fields, missing/duplicate gates, and measured-gate injection are rejected.
- Fail-closed decisions route missing/conflicting evidence, unmatched repairs, mixed repair directives, exhausted/unavailable retries, and binding conflicts to `manual_review`.
- Retry selection is restricted to the exact next available request fingerprint and one of four closed prequoted repair directives.
- `VideoBatchExecutor.apply_evaluation_decision` re-verifies the immutable artifact immediately before persisting the complete decision. `run_next` cannot submit a retry unless the persisted directive and fingerprint exactly match the next quoted attempt.
- Preserved Task 9 compatibility through its private handoff while converting the durable retry state to the new explicit decision shape.

## TDD and Verification Evidence

- Initial RED: `python3 -m unittest tests.test_video_evaluation_service tests.test_video_batch_executor tests.test_contracts -v` failed because `scripts.video_evaluation_service` and `schemas/shot_evaluation.schema.json` did not exist.
- Binding RED: the foreign-allowance regression returned `retry` instead of `manual_review` before exact allowance binding was enforced.
- Focused GREEN: 47 tests passed for evaluation, executor, and schema contracts.
- Task 10 generation-loop regression: 103 tests passed for evaluation, executor, generation planner, and batch allowance.
- Full regression: `python3 -m unittest discover -s tests -v` passed 528 tests in 20.870 seconds.
- Python compilation passed for the evaluation service, executor, and their tests.
- The valid evaluation fixture passed the repository shared JSON contract validator; all schemas parsed and the contract suite passed.
- `git diff --check` passed.

## Boundaries and Remaining Risk

- All media, model evaluations, allowances, and provider responses were synthetic. No network, real Dreamina invocation, paid request, native approval, release, or publication occurred.
- The environment does not provide the third-party `jsonschema` package, so validation evidence uses the repository's dependency-free Draft 2020-12 contract validator plus contract tests.
- Existing uncommitted Task 9 hardening in `dreamina_adapter.py` and `task_service.py` was preserved and excluded from this Task 10 commit.

## Fix Round 1: Production Allowance, Canonical Receipt, and Task 9 Cleanup

- Replaced fabricated allowance `repair_directive/state` consumption with the real allowance contract. Retry directive and fingerprint now come only from the immutable quote; availability comes from the activated allowance request list, state, and irreversible reservation history.
- Added a real `VideoBatchAllowance.activate/get/reserve/commit` integration test. The exact next prequoted retry is selected while available; reserved and exhausted states return `manual_review`.
- Replaced gate arrays with two closed objects keyed by all eight measured and all eight semantic gate names. Missing, extra, and wrong-domain keys are rejected by both service validation and the JSON schema.
- Defined one canonical receipt containing exact bindings, artifact receipt plus probe/frame evidence, both gate objects, failed gates, nested decision, evaluator provenance/timestamp, and a canonical fingerprint.
- Removed the action-only private persistence shape. `VideoBatchExecutor.apply_evaluation_decision` validates the full receipt and fingerprint, reloads quote and allowance, re-verifies the artifact immediately before the decision, checks decision/gate/directive consistency, and persists the whole receipt once.
- Carried the Task 9 ruling into `TaskService`: only `^\.verified-[a-f0-9]{32}\.tmp$` is cleanup-owned. Valid media named `clip.tmp` or `.verified-output.mp4` is verified and canonicalized without deletion; unsupported provider names fail without deletion.
- RED evidence: valid `.tmp`/`.verified-*` media was deleted; unsupported `.tmp` data was silently deleted; keyed-gate/evaluate APIs were absent; and executor rejected the required complete receipt shape.
- Final Task 10/9/8/7 focused regression: 182 tests passed in 0.844 seconds.
- Final full regression: 532 tests passed in 17.035 seconds.
- Python compilation, all-schema JSON parsing, shared contract validation, fixture fingerprint validation, distribution validation, secret-scan tests, and `git diff --check` passed.
- All evidence remained synthetic/local; no network, real Dreamina, paid request, native production approval, release, or publication occurred.
