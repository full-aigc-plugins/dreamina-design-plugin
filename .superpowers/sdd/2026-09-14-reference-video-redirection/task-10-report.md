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
- Final post-commit Task 10/9/8/7 focused regression: 184 tests passed in 0.866 seconds.
- Final full regression: 532 tests passed in 17.035 seconds.
- Python compilation, all-schema JSON parsing, shared contract validation, fixture fingerprint validation, distribution validation, secret-scan tests, and `git diff --check` passed.
- All evidence remained synthetic/local; no network, real Dreamina, paid request, native production approval, release, or publication occurred.

## Fix Round 2: Trusted Recalculation and Complete Manual Receipts

- `VideoEvaluationService` now requires a media adapter, an explicit trusted binding, and fresh `probe_json` evidence. Caller-supplied artifact probe/frame claims are ignored; artifact bytes are re-read and hashed.
- Missing/extra/wrong-domain semantic gates and artifact/design/semantic binding conflicts produce complete schema-valid `manual_review` receipts. Downstream unknown evidence is represented as bounded `skipped` gates instead of an exception or invented pass.
- Evaluator provenance is closed to `provider=codex`, strict RFC3339 time, and `trust_level=untrusted`; semantic model output remains structurally separate from trusted measured gates.
- `VideoBatchExecutor` accepts only the concrete production evaluation service, reloads the exact quote and allowance, re-verifies the content-addressed artifact, then calls `verify_receipt` to re-probe, rebuild every measured gate, re-derive retry availability, and compare the complete receipt before persistence.
- Added regressions proving malformed semantics persist a full manual receipt without retry and that a forged accepted receipt with a freshly recomputed public SHA-256 cannot pass trusted recomputation or reach durable state.
- RED evidence: the service had no trusted media-adapter/binding API; binding conflicts raised instead of returning manual receipts; and executor previously trusted a schema-valid public-hash receipt without media recomputation.
- Final Task 10/9/8/7 focused regression: 188 tests passed in 0.898 seconds.
- Final full regression: 536 tests passed in 21.438 seconds.

## Fix Round 2 Resume: Decoded Anchor Evidence and Final Verification

- Preserved the inherited five-file fix-round diff and completed its evidence trail without moving shared HEAD `ee56326`.
- Added enrolled-`ffmpeg` decoding at the start and end anchors. `frame_readability`, `start_anchor`, and `end_anchor` now come from actual decoder results rather than the mere presence of probe metadata; decoder errors remain bounded unavailable evidence.
- TDD RED: a temporary `git archive HEAD` with only the new behavior test failed 1/1 with `AttributeError: 'MediaAdapter' object has no attribute 'verify_video_frames'` (`RED_EXIT=1`). The same test passes against the working implementation.
- Immediate focused GREEN: `python3 -m unittest tests.test_media_adapter tests.test_video_evaluation_service tests.test_video_batch_executor -v` passed 41 tests in 0.228 seconds.
- Task 10/9/8/7 regression: `python3 -m unittest tests.test_video_evaluation_service tests.test_video_batch_executor tests.test_video_generation_planner tests.test_video_batch_allowance tests.test_video_service tests.test_task_service tests.test_operation_ledger tests.test_media_adapter -v` passed 180 tests in 0.885 seconds.
- Fresh full regression: `python3 -m unittest discover -s tests` passed 537 tests in 26.367 seconds.
- `py_compile` passed for the three affected production modules and their three focused test modules. The shot-evaluation schema and fixture parsed with `json.tool`; the shared fixture/schema/fingerprint contract test passed; distribution validation passed; both secret-scan tests passed; and `git diff --check` passed.
- Post-commit verification repeated the focused suite (41 tests in 0.270 seconds), full discovery (537 tests in 18.319 seconds), compilation, schema/fixture parsing, the contract plus secret-scan tests (3 tests in 0.053 seconds), distribution validation, `git diff --check`, and clean-worktree inspection.
- All verification remained synthetic/local. No network, provider request, credits, native approval, release, or publication occurred.

## Fix Round 3: Immutable Expected Media and Bound Frame Bytes

- Expected width, height, codec, duration, and aspect ratio now come from the exact immutable quote output profile and bound attempt request. Artifact receipt probe fields and caller design media claims cannot define the expected contract.
- Fresh probe observations are compared with that immutable contract. A 640x360 clip whose caller claim also says 640x360 fails dimensions against the quoted 1280x720 profile and routes to `manual_review`.
- The enrolled `ffmpeg` now decodes exactly one PNG at fixed bounded start/end positions into a private temporary directory. Each nonempty PNG/JPEG-signature-checked frame is bounded by the adapter output cap and recorded with `at_seconds`, SHA-256, and byte size.
- Frame evidence is part of `artifact_evidence.frames` and therefore the canonical receipt fingerprint. Trusted recomputation rebuilds both frame digests; empty output becomes unavailable/manual review, and a caller-rehashed receipt with a tampered frame digest is rejected before persistence.
- Preserved the concurrent Task 9/10 submit-identity hardening: canonical bindings include `submit_id`, and executor persistence verifies it against the awaiting task.
- TDD RED: five new targeted behaviors produced 2 failures and 2 errors on the prior implementation (boolean-only anchors, empty-output acceptance, quote-contract bypass, and schema rejection of digest-bearing frame evidence); only the existing unavailable-frame test passed.
- Focused Task 10/media/contracts GREEN: 61 tests passed in 0.258 seconds. Task 10/9/8/7 plus media/contracts regression passed 200 tests in 1.010 seconds.
- Full regression: `python3 -m unittest discover -s tests` passed 542 tests in 21.852 seconds.
- `py_compile`, schema/fixture JSON parsing, shared schema/fixture/fingerprint validation, distribution validation, two secret-scan tests, and `git diff --check` all passed.
- Evidence remains synthetic/local; no network, provider request, credits, native approval, release, or publication occurred.
