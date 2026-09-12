# Production runtime verification — 2026-09-13

## Scope

This check reused an existing successful Dreamina task. It performed no login
change, no generation submission, and no credit-consuming action.

## Evidence

- CLI build: `ec1b9fa-dirty` (`ec1b9fa`)
- Existing submit ID: `d208dadd-6600-4465-aaef-f3eba4044c86`
- Query command: `query_result --submit_id=<id>`
- Ledger mapping: `gen_status=success` → `state=succeeded`, `required_action=download`
- Download command: `query_result --submit_id=<id> --download_dir=<temporary-root>`
- Artifact: PNG, `3520 × 4693`, RGB, non-interlaced
- SHA-256: `2134a9c8ba8f3b545a5fe04d906eff5d3c3fcc4a88f208a3ba47c137dbae6170`
- Local `ArtifactService.verify_local` required the recorded
  `succeeded/download` ledger state and reproduced the same MIME, dimensions,
  and digest.

The artifact lived under `/tmp/dreamina-prod-audit.a7R01f/` and is not part of
the plugin distribution. This proves the installed CLI's existing-task
query/download path; it does not replace the separately approved paid canary.
