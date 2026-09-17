# Portable Agent Plugins migration

This repository currently uses the supported Codex compatibility manifest at `.codex-plugin/plugin.json`, matching the established Stitch plugin baseline.

The portable root `plugin.json` and `mcp.json` remain intentionally inactive. Add them only when the implementation can keep portable and compatibility metadata synchronized and can declare every transport, authentication, Skill, asset, and lifecycle behavior truthfully.

Migration acceptance requires schema validation, parity tests between both manifests, a fresh local installation, and confirmation that plugin identity remains `dreamina-design`.

