# Dreamina MCP full automation verification — 2026-09-13

## Implemented surface

- 11 closed-schema MCP tools.
- 8 generation modes: text/image generation, upscale, and five video modes
  including multi-frame and multimodal.
- Verified installer workflow, OAuth `flow_id`, account readiness, task
  query/list/download, Session CRUD, and bounded redacted diagnostics.

## Automated evidence

- Full repository suite: 255/255 passed before release validation.
- MCP inventory: exactly 11 tools.
- Unknown MCP properties: rejected before handler execution.
- Authentication: raw device code remains memory-only; random flow IDs expire
  after 600 seconds and are consumed once.
- Existing approval, ledger, reference, artifact, distribution, and security
  regressions remain green.

## Real read-only evidence

The trusted installed CLI was called through the MCP server without install,
authentication mutation, Session mutation, or paid generation.

- CLI identity: `ec1b9fa-dirty`.
- Capability modes: 8/8, including `image_upscale` and `multiframe2video`.
- Account readiness: successful redacted structured response.
- Task list with limit 1: successful structured response.
- Session list: successful response.
- Diagnostic scan: successful bounded response with no sensitive fields.

Existing separately authorized paid canaries remain the paid-runtime evidence;
this automation release did not submit another paid task.

## Security result

- No arbitrary shell, argv, installer URL, CLI path, or digest input exists.
- Installer execution requires fixed-host HTTPS, a bounded script response,
  digest-bound native approval, and post-install trust enrollment.
- Downloads require an approved root, an empty destination, native confirmation,
  regular-file checks, media signature checks, size bounds, and SHA-256 receipts.
- Logs are selected only from the fixed log root, opened with `O_NOFOLLOW`,
  bounded, and redacted before output.
- No Critical, High, or production-blocking Medium issue remained after review.
