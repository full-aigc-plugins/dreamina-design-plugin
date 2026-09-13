# Task 9 Report: Resumable Video Batch Execution

## Completion

- Added `VideoBatchExecutor.run_next`, `reconcile`, and query-only `resume`.
- Orders work by quote shot index and attempt number, gates retry attempts on an explicit evaluation-retryable predecessor, and bounds new submissions to 1..4.
- Persists batch/task state before reserve/invoke, query, and download side effects.
- Added an allowance-aware `VideoService` path while preserving the direct `submit` signature and approval flow.
- Timeout or missing submit ID consumes the reservation as ambiguous and enters manual review; no automatic retry occurs.
- Known submit IDs are queried before any further action; failed and unknown statuses are explicit.
- Downloads use `TaskService` verification and persist path, MIME, size, SHA-256, and external-query provenance.
- Batch execution state is atomic and survives process restart through `OperationLedger`.

## TDD and Verification Evidence

- RED: `python3 -m unittest tests.test_video_batch_executor tests.test_video_service tests.test_task_service tests.test_operation_ledger -v` failed with missing `scripts.video_batch_executor`.
- Focused GREEN: 124 tests passed with the required Task 9/allowance/service/task/ledger/adapter command.
- Full regression: `python3 -m unittest discover -s tests` passed 491 tests in 18.014s.
- `git diff --check` passed.

## Boundaries and Remaining Risk

- All adapters and artifacts in Task 9 tests are synthetic; no network, paid request, native approval, release, or real Dreamina call was made.
- A restart observed at the invocation boundary without a known submit ID fails closed to manual review.

## Fix Round 1

- Corrected verified downloads to persist `awaiting_evaluation`; retries remain closed until `record_evaluation_decision(..., "retry")` durably records an explicit evaluator decision.
- Added `BatchAllowanceCommitError`, preserving submit ID, reservation ID, request fingerprint, shot ID, and attempt when allowance terminalization is indeterminate.
- Provider-known timeout and commit-indeterminate paths persist allowance/reservation bindings in the operation ledger and resume by querying the known submit ID.
- `run_next` now reconciles every known nonterminal submit ID before any new reservation or provider submission, including after restart.
- RED evidence: focused tests failed on automatic `evaluation_retryable`, missing identity-carrying exception/state, and missing explicit evaluator-decision method.
- Additional RED evidence: known-ID timeout lacked a ledger receipt, terminal failure was overwritten by later work, and corrupt downloads escaped instead of persisting manual review.
- Final focused verification passed 146 tests; full discovery passed 499 tests in 25.256s. Python compilation and `git diff --check` passed.
- Fix-round focused regression: 138 executor/service/task/ledger/allowance/planner tests passed.
- Fix-round full regression: 499 tests passed in 24.589s; `py_compile` and `git diff --check` also passed.

## Fix Round 2

- Replaced loop-order-dependent global state assignment with a deterministic aggregate and explicit `required_action`.
- Aggregate precedence is `manual_review` over `failed`, blocking artifact/evaluation states, and active generation; retry eligibility is exposed only with no higher-priority unresolved task.
- Added two-known-task coverage for unknown/manual and failed plus querying, in both persisted task orders; both submit IDs are queried and new submissions remain blocked.
- RED evidence: aggregate API was absent and the last queried task incorrectly changed both dominant cases to `generating`.
- Final focused Task 9/8/7 regression: 140 tests passed in 0.693s.
- Final full regression: 501 tests passed in 15.983s; `py_compile` and `git diff --check` passed before the full run.
