# Task 5 — guarded synchronized review video

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
