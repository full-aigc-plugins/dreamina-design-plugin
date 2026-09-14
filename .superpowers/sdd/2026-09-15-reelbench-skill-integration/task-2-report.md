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
adds local trust and schema contracts only; subsequent adapter and sync-service
tasks must use `resolve_verified("browser")` for direct browser execution.
