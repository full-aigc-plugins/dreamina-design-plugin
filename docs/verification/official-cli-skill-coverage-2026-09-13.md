# Official Dreamina CLI Skill coverage — 2026-09-13

## Scope

The official v1.4.18 beginner guide was used as the command baseline. The
`dreamina-cli` umbrella Skill now covers the lifecycle from installation and
updates through authentication, account checks, generation, task follow-up,
downloads, Session CRUD, version reporting, and log-based troubleshooting.

## Covered command families

- installation/update: official `curl` installer
- discovery: root help, subcommand help, version
- authentication: interactive login, headless login, checklogin, relogin, logout
- account: user credit
- image: text-to-image, image-to-image, upscale
- video: text-to-video, image-to-video, frames-to-video, multi-frame, multimodal
- asynchronous operation: query, download, task history
- Session: create, list, search, rename, delete, generation in a selected Session

## Evidence

- The installed CLI accepted read-only help/version probes for all 22 relevant
  root, command, and nested-command entry points.
- Upstream repository tests: 43/43 passed in the isolated validator environment.
- Plugin repository tests: 228/228 passed after packaging.
- Plugin strict TRACE: 14/14 passed.
- The 13 packaged upstream Skill trees byte-match upstream commit
  `867d6edf2bb6975faeeefe560a36f57be1bbe021`.
- Official plugin validator and strict distribution/runtime gates passed.

No install, login-state mutation, Session mutation, or paid generation was
performed for this coverage update. Help/version probes establish command
availability; paid end-to-end evidence remains separately recorded in
`docs/verification/paid-canary-2026-09-13.md`.
