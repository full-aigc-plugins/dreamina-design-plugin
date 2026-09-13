# Official Dreamina command coverage

Date: 2026-09-13

Plugin version: `0.2.1`

Pinned Skill source:
`full-aigc-skills/dreamina-skills@e8ae5880fb36f71c4e60ef854060ab9cadd9edc4`

## Coverage

- The `dreamina-cli` umbrella covers installation, update, help/version,
  interactive and headless login, relogin/logout, account credit, async query,
  download, task history, Session CRUD, logs, and troubleshooting.
- `dreamina-cli-text2image`, `dreamina-cli-image2image`,
  `dreamina-cli-text2video`, and `dreamina-cli-image2video` own local CLI
  execution. Image upscaling is explicitly owned by the image-to-image Skill.
- Each execution Skill names its matching `dreamina-prompt-*` authoring Skill
  and `dreamina-opencli-*` alternative transport Skill. The routes are
  mutually exclusive at execution time to prevent duplicate paid submissions.
- Upstream machine-readable coverage maps all 25 official classic CLI command
  entries and all 39 official Canvas command entries to owning Skills.

## Verification

Upstream `tests/test_official_command_coverage.py` asserts complete command
sets, valid owning Skill identities, evidence tokens in the owning Skill
corpus, and existence plus routing of supporting Prompt/OpenCLI Skills.

The plugin's `tests/test_official_cli_skill_coverage.py` independently asserts
the complete install-to-use command list and the four execution-to-supporting
Skill routes after packaging.
