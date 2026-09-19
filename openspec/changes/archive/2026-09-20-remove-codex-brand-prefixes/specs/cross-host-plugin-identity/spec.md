## ADDED Requirements

### Requirement: Public identity SHALL be host-neutral

The plugin SHALL use its product or capability name without a `codex-` or `Codex-` prefix for public documentation filenames, document titles, skills, commands, examples, and cross-plugin references when the capability is available to more than one host.

#### Scenario: Reader opens an architecture document

- **WHEN** a reader follows the architecture or technical-solution link from the README
- **THEN** the filename and title identify the product plugin rather than a single host

### Requirement: Host-specific Codex contracts SHALL remain explicit

The plugin SHALL retain Codex naming where the identifier belongs specifically to the Codex host, including `.codex-plugin`, Codex CLI commands and environment variables, Codex-only build metadata, and declared migration compatibility protocols.

#### Scenario: Distribution validation inspects Codex metadata

- **WHEN** validation checks the Codex manifest or Codex build suffix
- **THEN** the canonical Codex-specific path and metadata remain unchanged

### Requirement: Renames SHALL preserve navigability

Every renamed file or identifier referenced inside the repository SHALL have all current inbound links and validation fixtures updated in the same change.

#### Scenario: Documentation links are checked after migration

- **WHEN** repository Markdown links are resolved
- **THEN** no current link points to a removed Codex-prefixed public filename
