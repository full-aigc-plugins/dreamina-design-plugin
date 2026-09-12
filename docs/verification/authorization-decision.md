# Paid-action authorization status

## Current state

- request fingerprint and displayed scope binding: implemented
- five-minute maximum local approval lifetime: implemented
- random opaque approval ID: implemented
- atomic single-use consumption: implemented and concurrency-tested
- caller-supplied raw approval dictionary accepted by submit: prohibited
- trusted human approval issuer from Codex UI/MCP: implemented through paid MCP tools with `approval_mode: prompt`
- paid canary: **NOT_RUN**

`record_approval()` is not exposed as the production entry point. The paid MCP
tool call is gated by the Codex product confirmation prompt; after acceptance,
the handler derives the exact scope and issuer internally, issues a random
maximum-five-minute approval ID, and consumes it once in the same workflow.

No document or automated test may convert the still-unrun paid canary into PASS.
