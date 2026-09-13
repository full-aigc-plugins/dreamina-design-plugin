# Dreamina CLI runtime evidence

> Read-only runtime contract: observed
> Paid canary: APPROVED and completed

## Observed contract

- executable: `/Users/wandl/.local/bin/dreamina`
- build identity: `ec1b9fa-dirty` (`ec1b9fa`)
- trusted executable SHA-256: `454653ac83291df908fc8d541ab715659bc9c7e4c2b8e907d5d255faa8fe0e87`
- capability source: top-level and per-command help because this build has no
  top-level `schema` command
- account readiness: confirmed without recording identity, credential, or balance
- generation performed during capability check: no

The capture files are `cli-version.txt`, `cli-help.txt`, and
`cli-schema.json`. The latter is a structured `command-help` snapshot with the
required generation, session, history, and query commands.

Paid/query execution through `DreaminaAdapter` requires the absolute binary
path and this administrator-reviewed SHA-256. PATH lookup and inherited ambient
environment variables are disabled.

## Existing-task recovery proof

An existing successful task was queried and downloaded without submitting a
new generation. See `production-runtime-2026-09-13.md` for submit ID, media
dimensions, and SHA-256 evidence.

## Skill source proof

The 13 packaged Design Skill trees byte-match upstream Git commit
`300bfc1d649a68c1802a43aa7a64c50000e095d4`. Verification reads blobs from the
pinned Git object rather than trusting a mutable working-tree HEAD.

## Paid boundary evidence

The authorized minimum-specification canary reached `success`; see
`paid-canary-2026-09-13.md`. Production MCP calls no longer accept a caller
controlled executable. The trusted CLI is enrolled through a native dialog and
stored in a user-owned `0600` configuration; each paid request additionally
requires a native dialog whose default action is Cancel.
