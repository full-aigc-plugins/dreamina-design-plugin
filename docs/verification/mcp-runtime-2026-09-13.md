# MCP runtime verification — 2026-09-13

A disposable local marketplace installed the current plugin candidate and a
fresh ephemeral Codex task called
`dreamina_design.dreamina_capability_snapshot` exactly once.

Observed result:

- MCP initialization and `tools/list`: PASS
- verified binary staging and SHA-256 check: PASS
- CLI version: `ec1b9fa-dirty`
- capability modes: 6
- paid submit tools called: no
- shell fallback used: no
- generation or credit consumption: no

The plugin `.mcp.json` config sets `approval_mode: prompt` for both
`dreamina_submit_image` and `dreamina_submit_video`. Their MCP annotations are
`readOnlyHint=false`, `destructiveHint=true`, and `idempotentHint=false`.

The disposable plugin and marketplace were removed after the check. Paid-tool
prompt behavior will be exercised only as part of the separately approved
minimum-cost canary.

## Post-canary hardening

The canary revealed that a Codex CLI host with global `approval: never` can
execute the paid MCP tool without a plugin-level prompt. The server therefore
no longer treats host approval as its only boundary:

- MCP inputs contain no executable path or digest;
- a native trust-enrollment dialog fixes the CLI path/owner/SHA-256 in a
  user-owned `0700/0600` configuration;
- every paid request requires a second native dialog with default Cancel;
- native approve and deny paths were both exercised without generation;
- the no-argument capability tool successfully loaded only the enrolled CLI
  and returned six modes.
