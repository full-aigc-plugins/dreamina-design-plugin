# Task 3 Report: Private Versioned Video Projects and Source Intake

## Status

- Implemented private project storage, compare-and-swap state transitions, monotonic version allocation, source-video intake, closed JSON contracts, native approval binding, and security regression tests.
- Corrected the adopted test fixture and project schema to the authoritative values `authorized_replication` and `preserve_authorized_audio`.

## RED evidence

Command (Task 3 production modules and schemas were temporarily moved aside, then restored unchanged):

```bash
python3 -m unittest tests.test_video_project_store tests.test_media_intake_service tests.test_contracts -v
```

Result: expected RED. `Ran 13 tests`; `FAILED (failures=1, errors=5)`. The failures were specifically missing `scripts.video_project_store`, missing `scripts.media_intake_service`, and missing `video_project.schema.json` / `source_receipt.schema.json`.

## GREEN evidence

Command:

```bash
python3 -m unittest tests.test_video_project_store tests.test_media_intake_service tests.test_reference_policy tests.test_contracts -v
```

Result: `Ran 24 tests in 0.102s` and `OK`, including two-process version locking, state conflict handling, source trust checks, private permissions, duration limits, approval denial durability, and schema checks.

## Full-suite evidence

The repository root is not a unittest discovery package, so `python3 -m unittest discover -v` reported `Ran 0 tests`. The effective repository suite command was then run:

```bash
python3 -m unittest discover -s tests -v
```

Result: `Ran 302 tests in 12.543s` and `OK`.

## Files

- `schemas/video_project.schema.json`
- `schemas/source_receipt.schema.json`
- `scripts/video_project_store.py`
- `scripts/media_intake_service.py`
- `tests/test_video_project_store.py`
- `tests/test_media_intake_service.py`
- `tests/test_contracts.py`

## Self-review

- Contract validation is applied before project persistence and before source-receipt versioning.
- Project roots and mutable JSON files are private (`0700` / `0600`); staged source bytes are immutable to the owner (`0400`).
- Project transitions hold an exclusive file lock and reject stale expected states and forbidden edges.
- Version allocation occurs under the same project lock and uses atomic replace plus directory fsync.
- Intake rejects relative paths, symlinks, paths outside approved roots, unsupported container signatures, changed inodes, oversized/duration-exceeded media, and malformed probe metadata.
- Native confirmation receives the source digest and declared processing purpose before the receipt is recorded.
- Repeat-intake denial does not delete an already durable staged source.
- `git diff --check` completed without whitespace errors before final verification.

## Concerns

- Task 3 implementation was committed concurrently as `457b7a5` while this task was running. That commit was preserved; the authoritative enum correction is recorded separately rather than rewriting shared history.
- Full-suite discovery requires `-s tests`; default root discovery currently finds zero tests.
