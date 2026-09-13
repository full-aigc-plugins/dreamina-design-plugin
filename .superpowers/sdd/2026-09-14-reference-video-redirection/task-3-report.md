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

## Fix round 1 — 2026-09-14

### Findings closed

- Removed the public `probed_duration_seconds` override. Duration is now accepted only from complete `MediaAdapter.probe_json` metadata.
- Restricted intake to byte-identified MP4, MOV, and structurally parsed WebM. Matroska is rejected, and byte type must agree with ffprobe `format_name`.
- Added the required `version` field to the closed receipt contract. `write_version(..., schema_name=...)` validates the final versioned document before atomic persistence, and intake returns that exact stored document.
- Enforced exact creative-mode and audio-policy enums, with all allowed and obsolete values covered.
- Counted bytes during copying with immediate typed limit failure; source device, inode, and size are rechecked after copying and probing, including same-inode growth.
- Kept every intake attempt private until native approval. Content-addressed publication is serialized by `flock`; the final read-only file is re-opened with `O_NOFOLLOW`, rehashed, and inode/size checked immediately before receipt persistence.

### RED evidence

Initial fix-round focused command:

```bash
python3 -m unittest tests.test_video_project_store tests.test_media_intake_service -v
```

Result: exit 1, `Ran 15 tests`, `FAILED (failures=5, errors=5)`. Failures proved the obsolete audio enum, caller-controlled duration, missing stored version, EBML/MKV misclassification, copy-growth limit bypass, and approve/deny publication race.

Strengthened EBML boundary:

```bash
python3 -m unittest tests.test_media_intake_service.MediaIntakeServiceTests.test_accepts_webm_and_rejects_matroska_or_probe_disagreement -v
```

Result: exit 1. A `webm` marker outside an empty declared EBML header was incorrectly accepted.

Complete-probe boundary:

```bash
python3 -m unittest tests.test_media_intake_service.MediaIntakeServiceTests.test_intake_rejects_incomplete_audio_probe_metadata -v
```

Result: exit 1. Missing audio channel metadata was incorrectly defaulted and accepted.

### GREEN and verification evidence

```bash
python3 -m unittest tests.test_video_project_store tests.test_media_intake_service tests.test_reference_policy tests.test_contracts -v
```

Result: exit 0, `Ran 32 tests in 0.123s`, `OK`.

```bash
python3 -m unittest discover -s tests -v
```

Result: exit 0, `Ran 310 tests in 11.765s`, `OK`.

`python3 -m compileall -q` for both changed scripts and both Task 3 test modules exited 0. `git diff --check` also exited 0.

### Fix-round self-review

- Source attempt files remain `0600` and unshared until approval; denial can only remove its own attempt path.
- The publish lock serializes identical-digest finalization and receipt persistence, so a denied attempt cannot remove a file accepted by another attempt.
- The receipt's final `version`, staged path, media fields, and approved-root digest are validated as one closed document before writing.
- No shot analysis, shell command, network request, paid action, release operation, or unrelated runtime modification was introduced.
- Remaining validation boundary: tests use complete deterministic ffprobe payloads; the existing media-adapter tests separately verify trusted argv-only ffprobe execution.

## Fix round 1 evidence

### RED

Command:

```bash
python3 -m unittest tests.test_video_project_store tests.test_media_intake_service tests.test_contracts -v
```

Result: expected RED, `Ran 26 tests in 0.114s`, `FAILED (failures=5, errors=5)`. Failures covered the obsolete audio enum, caller-controlled duration bypass, WebM misclassification, unversioned returned/persisted receipt, unchecked stream growth, and concurrent denied-intake deletion of an accepted identical source.

### GREEN

Command:

```bash
python3 -m unittest tests.test_video_project_store tests.test_media_intake_service tests.test_contracts tests.test_reference_policy tests.test_json_contracts -v
```

First result: `Ran 42 tests in 0.227s`, `FAILED (failures=1)` because incomplete audio-stream metadata was still defaulted instead of rejected. After making audio metadata fail closed, the exact same command returned `Ran 42 tests in 0.122s`, `OK`.

### Full suite

Command:

```bash
python3 -m unittest discover -s tests -v
```

Result: `Ran 310 tests in 13.940s`, `OK`.

### Fixes and regression coverage

- Removed `probed_duration_seconds` from the production intake API; only trusted probe duration controls the 1800-second gate.
- Added bounded EBML-header parsing for `DocType=webm`, rejected Matroska/MKV and malformed/spoofed headers, and cross-checked byte identity against trusted probe format names.
- Added `version` to the closed source-receipt schema and validate the final numbered document before atomic persistence.
- Enforced exact creative/audio policy enums, including all four approved audio policies and rejection of obsolete tokens.
- Counted bytes during streaming, enforced the size ceiling during copy, and compared copied bytes plus descriptor/path identity and size to the initial stat.
- Kept intake attempts private and unpublished until native approval, then serialized final digest publication and receipt persistence; a denied concurrent attempt can only delete its own temporary file.
- Added fail-closed coverage for incomplete audio-stream probe metadata.

### Self-review

- Approval occurs only after trusted probe, type agreement, duration limit, digest computation, and stability checks.
- The returned receipt is the exact versioned document persisted on disk and passes `validate_contract`.
- Final staged files are content-addressed and `0400`; attempt files remain private and are removed in `finally` on approval denial or any failure.
- Concurrent accepted identical content is verified by digest and size before receipt persistence, and denied attempts never own the durable path.
- `git diff --check` passed before commit preparation.

### Remaining concerns

- The trusted adapter's `format_name` for ISO BMFF commonly reports the combined `mov,mp4,...` demuxer identity, so MOV versus MP4 is derived from the `ftyp` major brand while the probe cross-check validates the shared trusted demuxer family.

## Fix Round 2 — ISO BMFF Exactness and Durable-Path TOCTOU

### RED

```bash
python3 -m unittest tests.test_media_intake_service.MediaIntakeServiceTests.test_iso_bmff_probe_agreement_is_exact_unless_probe_reports_combined_family tests.test_media_intake_service.MediaIntakeServiceTests.test_staged_path_replacement_after_hash_is_rejected_before_receipt -v
```

Result: exit 1, `Ran 2 tests in 0.017s`, `FAILED (failures=3)`. QuickTime bytes with an `mp4`-only probe and MP4 bytes with a `mov`-only probe were both incorrectly accepted; replacing the final staged pathname at hash EOF also escaped detection and allowed receipt persistence.

### GREEN

The exact same targeted command returned exit 0, `Ran 2 tests in 0.009s`, `OK`.

Task 3 focused verification:

```bash
python3 -m unittest tests.test_video_project_store tests.test_media_intake_service tests.test_contracts tests.test_reference_policy tests.test_json_contracts -v
```

Result: exit 0, `Ran 44 tests in 0.133s`, `OK`.

Full suite:

```bash
python3 -m unittest discover -s tests -v
```

Result: exit 0, `Ran 312 tests in 10.013s`, `OK`.

### Changes and self-review

- Byte-level `ftyp` identity remains authoritative. A normal combined `mov,mp4,...` probe family accepts either byte-identified MOV or MP4, while a single `mov` or `mp4` token must exactly match the byte identity. WebM still requires the `webm` probe token.
- Durable-file verification now counts hashed bytes, performs a second descriptor `fstat`, then re-`lstat`s the pathname and requires the initial path stat, opened descriptor, final descriptor stat, and final pathname to agree on device, inode, and expected size before any receipt write.
- The injected pathname-replacement regression swaps the final path immediately after hash EOF and proves failure occurs while the receipt directory is still absent.
- No Task 1/2 behavior or contract was modified.

### Concerns

- None specific to Round 2. The existing project-local publish lock remains necessary so the durable path cannot be legitimately replaced by a competing intake during this final verification window.
