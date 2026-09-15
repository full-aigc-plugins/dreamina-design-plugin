# Task 5 — guarded synchronized review video

## Fix round 1 takeover (supersedes the original scope claims below)

The old mapping-based service has been replaced. Public actions resolve a project
ID and exact validated Task 3 evidence version; callers cannot supply source,
shots, workspace descriptors, layout/probe mappings, or arbitrary output paths.
Source, consumed documents and ancestor artifacts are checked and copied into a
private workspace. Upstream `plan` is the only geometry producer.

The sync publication journal is a namespace-isolated adaptation of Task 3's
sealed operation/cleanup protocol. Panels and MP4s publish under immutable
`reelbench_sync_media/vNNN/` directories and `reelbench_sync/vNNN.json` receipts.
Visible publication failures retain exact typed recovery information.

Real Node child-process testing proved that inherited non-stdio FDs disappear
at `execFileSync`. A fixed, independently pinned Node preload preserves only
the browser lease descriptors for the exact relative browser proxy. All other
child processes retain their original behavior. Script, asset, preload and
proxy digests are checked independently from the mutable upstream lock.

Two upstream runtime discrepancies required controller-approved adapter bridges:

- Pinned upstream audio is AAC re-encoded, not copied. Preserved-audio exports
  therefore use a separate trusted remux candidate, then compare per-stream
  source/final packet hashes and stream metadata before publication.
- Looped PNG composition does not terminate for silent inputs with upstream's
  `-shortest` alone. A fixed sync-only FFmpeg proxy validates the command shape
  and adds a measured source-duration bound. Both upstream and expanded argv
  plus the expansion fingerprint are recorded in ordered provenance.

Silent exports first create a no-audio derivative. Final verification actually
probes the MP4 and PNGs and decodes source/output/highlight pixels; no caller
probe mapping or hard-coded successful correspondence gate remains. Actual
composition and final-export consumers reject review artifacts.

Verification checkpoint before archive validation:

- Focused sync/adapter/final/composition regression: 65 tests PASS.
- Real temporary trusted-store fixture: silent export plus re-verification PASS;
  preserved-audio export and exact source/final packet hashes PASS.
- Upstream selftests unchanged: 122 sync / 449 shots assertions PASS.
- Real browser tests are opt-in with `REELBENCH_REAL_SYNC=1`; they use only
  temporary trust/configuration/project directories and never change host
  enrollment. The strengthened two-cut fixture and immutable archive full
  regression remain to be recorded in the final verification section.

The fixed sampling verifier fails closed when a shot has no stable full frame
after upstream's highlight easing. This is a typed verification limitation,
not evidence of successful correspondence for that shot.

## Scope delivered

- `ReelBenchAdapter` now stages only the pinned `video-sync.mjs` plus its
  required unchanged `panel.css` and `panel.html` bytes, selected explicitly
  through `workflow="sync"`.  Its `plan`, `panels`, and `export` invocations
  use fixed relative workspace argv and the existing bounded process runner.
- `ReelBenchSyncService` calculates upstream-compatible source-aspect geometry;
  requires an exact validated ReelBench evidence receipt with matching source
  digest/version; validates the layout receipt, continuous cuts, exactly three
  panel PNGs, H.264/yuv420p dimensions/duration, explicit audio policy, and
  per-shot cut/sample correspondence.
- Every verified artifact is labelled only `synchronized_review`.  The service
  raises `FinalMediaVerificationError` when that receipt is offered to a
  generated/final-media boundary.
- Browser re-verification is mandatory before `panels` and `export`. A missing
  or unverified browser returns `BLOCKED_MISSING_TRUSTED_BROWSER`; it does not
  produce a success receipt. Sync receipt persistence uses immutable
  `reelbench_sync/vNNN.json` versions and exact-fingerprint recovery.

## TDD evidence

RED was observed before implementation:

```text
python3 -m unittest tests.test_reelbench_sync_service -v
ModuleNotFoundError: No module named 'scripts.reelbench_sync_service'
```

GREEN/verification run after implementation:

```text
python3 -m unittest tests.test_reelbench_sync_service tests.test_reelbench_adapter tests.test_final_media_service -v
Ran 33 tests ... OK

node skills/dreamina-video-sync/scripts/selftest.mjs
122 assertions passed

python3 -m py_compile scripts/reelbench_sync_service.py scripts/reelbench_adapter.py
git diff --check
```

## Real-host prerequisite evidence

Executables are present locally: Node
`/Users/wandl/.nvm/versions/node/v24.18.0/bin/node`, FFmpeg and ffprobe under
`/opt/homebrew/bin`, and Chrome at
`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.

The trusted-media store currently reports all four as
`NOT_ENROLLED_OR_UNVERIFIED`. Therefore no real local fixture was run: the
correct observed status is blocked prerequisites, not a fabricated review-video
PASS. No package installation, paid Dreamina operation, or arbitrary-root
execution occurred.

## Remaining integration boundary

Task 6 must expose the closed service actions through MCP and supply its owned
project workspace/validated evidence inputs. A fresh native enrollment is
required before a real browser/FFmpeg fixture can run.
