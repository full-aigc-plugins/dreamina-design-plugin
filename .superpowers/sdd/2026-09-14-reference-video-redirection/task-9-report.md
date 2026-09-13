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
