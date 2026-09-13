# Paid canary verification — 2026-09-13

## Authorization

The user explicitly authorized one minimum-specification image canary in the
controlling Codex task. Scope was limited to one image.

The initial Codex CLI host reported global `approval: never`, so its plugin MCP
`approval_mode: prompt` did not display a second prompt. This host behavior is
recorded as a security finding; the production handler now additionally
requires a server-side native macOS dialog with default **Cancel**.

After hardening, the native provider was exercised separately without calling
Dreamina:

- click **批准一次** → `native-user-confirmed`
- click **取消** → `ApprovalDeniedError`, fail closed

## Generation result

- submit ID: `bff07abc-a2f6-473b-b53d-38c7bd7b492c`
- mode: `text2image`
- model: `5.0Pro`
- resolution: `1.5k`
- ratio: `1:1`
- count: 1
- terminal state: `success`
- query history: 13 query-only observations; no resubmission
- commerce credit count: 0
- benefit type: `image_basic_v50_pro_15k`

## Artifact result

- first download: failed with `ret=1015`
- recovery: retried `query_result` with the same submit ID; no generation retry
- format: PNG
- dimensions: 1536 × 1536
- size: 199,913 bytes
- SHA-256: `c4b3d50cdd9e342ac0ac9c822d78453b76a8b58cac5863a1e7eafb993aa56e38`
- operation-bound local artifact verification: PASS

The artifact remains under a temporary directory and is not distributed with
the plugin.
