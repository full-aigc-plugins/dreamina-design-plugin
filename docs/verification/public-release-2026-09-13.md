# Public release verification — 2026-09-13

## Publication

- plugin version: `0.2.0`
- public marketplace: `partme-ai-dreamina-design`
- plugin selector: `codex-dreamina-design@partme-ai-dreamina-design`
- source: `https://github.com/partme-ai/partme-dreamina-design.git`, ref `main`

## Installed cache

- 14/14 Skills discovered
- complete Skill bodies present; no `Stub body` marker
- manifest declares `.mcp.json`
- both paid tools require `approval_mode: prompt`
- server-side native approval and trusted CLI enrollment are packaged

## Public MCP runtime

A fresh ephemeral Codex task used only the public installation and called
`dreamina_design.dreamina_capability_snapshot` with `{}` arguments.

- server: `dreamina_design`
- tool: `dreamina_capability_snapshot`
- caller-controlled CLI path/hash: absent
- CLI version: `ec1b9fa-dirty`
- mode entries: 6
- paid tools invoked: no

The final local/tracking/remote SHA equality is recorded after this evidence
commit is pushed and the public plugin is reinstalled once more.

## User acceptance

The user explicitly confirmed on 2026-09-13 that functional verification was
complete and passed. The acceptance scope and evidence boundaries are recorded
in `docs/verification/user-functional-acceptance-2026-09-13.md`.
