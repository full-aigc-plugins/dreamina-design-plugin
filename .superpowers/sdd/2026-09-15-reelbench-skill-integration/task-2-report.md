# Task 2 Report: trusted ReelBench identities and closed contracts

## RED

Ran before implementation:

```text
python3 -m unittest tests.test_trusted_media_tools tests.test_contracts -v
```

The new trusted-tool tests failed because `browser` was an unsupported kind and
`SYSTEM_BROWSER_EXECUTABLES`/`resolve_verified` did not exist. The new contract
tests failed because both ReelBench schema files were absent.

## GREEN

Ran after implementation:

```text
python3 -m unittest tests.test_trusted_media_tools tests.test_contracts tests.test_native_approval -v
# 46 tests passed

python3 -m unittest tests.test_media_adapter tests.test_reference_video_service -v
# 43 tests passed

git diff --check
```

## Security cases covered

- `node` remains user-enrolled and is revalidated by exact path, owner, and SHA-256.
- `browser` accepts only an explicit Chrome/Chromium/Edge executable allowlist;
  name matching is never used.
- Browser records pin path, owner, mode, device, inode, size, and SHA-256.
  A permission change or same-byte replacement fails closed.
- Symlink, unsafe owner, unsafe permissions, unexpected config fields, and an
  unapproved browser path are rejected. Root ownership is accepted only for an
  exact allowlisted system/browser path.
- Evidence gates use the distinct uppercase values `PASS`, `FAIL`, and
  `SKIPPED`; both evidence and comparison contracts reject unknown fields.

## Commit

`feat: define trusted ReelBench evidence`

## Concerns

No live Node/browser enrollment or artifact production was performed. This task
adds local trust and schema contracts only; the future sync service must use
the launch-bound browser re-verification seam.

## Fix round 1

RED: the new tests failed because `BrowserApplicationPolicy`, the launch-bound
browser re-verification seam, and the ReelBench semantic validator did not
exist.

GREEN: `python3 -m unittest tests.test_trusted_media_tools
tests.test_reelbench_contracts tests.test_contracts tests.test_native_approval
tests.test_media_adapter tests.test_reference_video_service -v` passed 96 tests.

- Restored the exact legacy `enroll() -> dict` contract; only
  `resolve_verified()` returns `TrustedExecutable`.
- Node and browser records now pin mode, device, inode, size, owner, path, and
  SHA-256. Node staging copies from the same descriptor that was opened,
  hashed, fstat-checked before/after, and rechecked against the final pathname.
- Browser launch uses `reverify_browser_for_launch()`, exact macOS application
  policy, fixed `/usr/bin/codesign` argv, and full identity/signature checks.
  Browser updates require fresh enrollment.
- Added semantic fingerprint, RFC3339, artifact-path, gate-set, and comparison
  contradiction checks. A `manual_review` domain requires overall
  `manual_review`.

## Fix round 2

Added copied-byte SHA-256/size checks and mtime/ctime FD identity checks during
Node staging. Added the one-shot descriptor-owned `BrowserLaunchHandle`, strict
sync/shot gate semantics, and canonical artifact-path rejection. Focused trust,
contract, and compatibility tests passed (46 tests).
