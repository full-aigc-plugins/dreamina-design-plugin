# Codex Dreamina Design Plugin Design

## Goal

Package the migrated Dreamina prompt and CLI Skills into safe image/video generation workflows with current capability discovery, explicit approval, and resumable async operations.

## Requirements

- Complete `jimeng-*` to `dreamina-*` identity migration before packaging.
- Treat current CLI help/schema as the parameter authority.
- Cover image, image editing, text/image/frame/multimodal video, sessions, history, query and download.
- Bind user approval to the exact request and never resubmit unknown operations.
- Keep paid runtime acceptance separate from offline validation.

## Non-goals

No private API client, browser bypass, hidden login, fixed model catalog, automatic credit approval, or 3D DCC automation.
