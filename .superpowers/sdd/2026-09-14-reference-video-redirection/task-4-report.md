# Task 4 Report: Deterministic Reference Video Analysis

## Scope

Implemented only local deterministic reference-video measurement: trusted
ffprobe/ffmpeg probing, scene candidates, normalized cuts, a separate motion
track, measured shots, 15%/85% frames, contact sheets, machine fingerprinting,
and typed recuts. No semantic/model annotation, ASR, network, Dreamina
generation, paid operation, or release behavior was added.

## TDD evidence

- RED: `python3 -m unittest tests.test_reference_video_service tests.test_contracts -v`
  ran 13 tests and failed with the expected missing
  `scripts.reference_video_service` module and `shot_analysis.schema.json`.
- GREEN: the same focused command ran 20 tests successfully after the minimal
  implementation.
- A second RED/GREEN cycle corrected start-boundary provenance and verified
  semantic preservation only for byte-for-byte unchanged shot intervals.

## Implementation

- Added a closed Draft 2020-12 shot-analysis schema.
- Added bounded Python-native analysis using fixed argv through `MediaAdapter`.
- Stored motion samples in private `track.json`, outside the analysis document.
- Stored immutable numbered analysis versions through
  `VideoProjectStore.write_version`.
- Bound source receipt, parameters, cuts, measured shots, and frame checksums
  into `machine_fingerprint`.
- Enforced scene threshold `0.05..0.80`, minimum seed shot `0.10..5.00`, track
  rate `1..10 Hz`, frame width `240..960`, at most 200 recut operations, and at
  most 25 shots on a contact-sheet page.
- Recut recomputes contiguous two-decimal boundaries, stable sequential shot
  IDs, durations, motion medians, and provenance; it clears semantics for
  changed intervals and retains semantics for exactly unchanged intervals.

## Verification

- Focused integration: 28 tests passed.
- Full suite: 321 tests passed in 15.015 seconds.
- Python compilation and `git diff --check`: passed.
- External ReelBench repository HEAD matched
  `75520c7b32ab5af8b22c5e4f79705efbbc0d8e07`; its selftest passed all 449
  assertions across 15 gates.

## Attribution and remaining boundary

`THIRD_PARTY_NOTICES.md` records ReelBench inspiration at the pinned commit and
states that no ReelBench source or media was copied. Media subprocess behavior
is tested at the adapter boundary; the plugin still requires separately
enrolled real ffmpeg/ffprobe binaries for host execution.
