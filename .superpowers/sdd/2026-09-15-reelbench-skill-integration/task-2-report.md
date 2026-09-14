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

## Fix round 3

The browser handle now launches the allowlisted bundle-relative executable via
an inherited bundle directory FD and fixed isolated Python helper rather than
executing `/dev/fd/<n>`. Focused 46-test regression remains green.

Additional Round 3 commit routes codesign and the browser helper through the
shared argv-only bounded process runner with minimal environment, caps, timeout,
process-group termination, and descriptor cleanup.

### Round 3 takeover audit and completion

Audited partial commits `ffdb352` and `35cd1c7` against the Task 2 brief, design,
and ledger rulings. The initial runner still buffered all output with
`communicate()`. The legacy launch method returned an unowned raw-path context;
invalid arguments and construction failures leaked handles; closed handles could
be reused; real application permissions remained unsupported.

RED evidence before fixes:

- `tests.test_bounded_process`: 14 tests, 5 failures and 7 errors; reproduced
  delayed output limits, multibyte cap bypass, invalid limit handling, descendant
  survival after overflow, and missing selector-setup cleanup.
- Browser tests reproduced invalid-argument/construction FD leaks, reuse after
  explicit close, raw launch-context return, executable replacement, and rejected
  real macOS parent permissions.
- The exact macOS permission matrix then reproduced 4 failures for valid
  admin-group 0775 application executable/bundle ancestors before that narrow
  signed-application exception was added.

Implemented and covered:

- Incremental nonblocking stdout/stderr reading with independent byte caps;
  finite positive deadlines include EOF-with-live-process and descendants that
  retain pipes after leader exit. Failure and success cleanup terminate the owned
  process group, close the selector/pipes, and reap the leader. Caller-owned
  `pass_fds` are retained by the caller. Nonzero status and bounded UTF-8 decoding
  are preserved.
- Both browser launch entry points now return a one-use owned handle. Invalid
  arguments/limits, explicit close, context exceptions, failed spawn, abandonment,
  and partial construction release its descriptors. Context metadata is only an
  audit receipt, not a returned launch path.
- The isolated system-Python helper starts from the inherited bundle directory
  FD, opens bundle descendants with `O_NOFOLLOW`, checks the executable against
  the inherited file FD and digest, and launches from the pinned MacOS directory.
  A replaced external ancestor cannot redirect launch. Replaced executable or
  symlinked internal bundle directories are rejected before execution.
- The bundle is opened before signature verification and its final identity is
  compared after verification. Codesign uses fixed argv, a minimal environment,
  10 seconds and independent 64 KiB byte caps. Timeout, overflow, nonzero result,
  missing fields, wrong identifier/team/requirement fail closed.
- Exact allowlisted macOS browser paths support owner root/current user and
  admin group 80 with mode 0775 for the executable and exact bundle ancestors;
  `/Applications` itself must be root-owned for this exception. World-write,
  foreign owner/group, arbitrary paths, symlink aliases, and unrelated writable
  parents remain rejected. Existing executable mode/inode/size/digest pinning
  remains mandatory; signature validation is required at enrollment and launch.
- Node copied-byte digest checking remains covered by a new same-size in-place
  mutation after verified open. Legacy enrollment dictionary behavior remains.
- Sync verification tests use the literal exact six-set ending with
  `sampled_correspondence`; missing, extra, duplicate, old alias and SKIPPED gates
  are rejected. FAIL remains distinct measured evidence. Existing shot gate and
  closed-contract semantics remain unchanged.

Verification:

- 58 new tests: 36 browser/policy, 14 bounded-runner, 7 sync-gate, 1 Node copy.
- Focused trust/contracts/native-approval suite: **111 passed**.
- Media-adapter/reference-video compatibility suite: **43 passed**.
- Full offline suite `python3 -m unittest discover -s tests -q`: **861 passed**
  in 105.881 seconds (expected negative CLI fixtures print usage errors).
- `python3 -m compileall -q scripts tests`: passed.
- `git diff --check`: passed.

Live macOS read-only/runtime evidence:

- Installed Chrome executable and .app/Contents/MacOS ancestors observed as
  `wandl:admin 0775`; `/Applications` observed as `root:admin 0775`.
- Exact identity and actual codesign strict verification/details succeeded:
  identifier `com.google.Chrome`, team `EQHXZ8M8AV`, configured designated
  requirement exactly matched.
- Actual bundle-FD launch helper executed Chrome `--version` and returned
  `Google Chrome 153.0.8010.36`, exit 0. The test used an in-memory trust record;
  no persistent enrollment configuration was written and no browser profile or
  media artifact was created by the task.
- Edge and Chromium are not certified by this run; the exact policy/signature
  checks are synthetic coverage for absent applications. No paid calls,
  dependency installs, release or Marketplace actions occurred.

Remaining scope: this task proves trust/runner/receipt contracts, not a complete
ReelBench panel/export MCP flow. Task 5 must consume the handle operation. The
selected macOS design assumes the current user and system administrators remain
trusted while executing a signed application; it does not claim isolation from
an administrator modifying the live bundle concurrently after the last check.
