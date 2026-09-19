# host-runtime-loading Specification

## Purpose
TBD - created by archiving change fix-host-runtime-loading. Update Purpose after archive.
## Requirements
### Requirement: Dreamina MCP launcher is path portable

Codex and ZCode SHALL resolve `python3` from PATH and SHALL NOT require `/usr/bin/python3`.

#### Scenario: Load outside `/usr/bin`

- **WHEN** Python 3 is available on PATH in a supported host
- **THEN** the Dreamina MCP can start without an absolute interpreter path

