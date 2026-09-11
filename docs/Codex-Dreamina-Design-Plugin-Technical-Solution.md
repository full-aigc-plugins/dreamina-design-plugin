# Codex Dreamina Design Plugin Technical Solution

## Decision

Build a skills-first compatibility plugin around the installed `dreamina` CLI. Use a shared subprocess adapter, capability snapshot, approval guard, operation ledger, and artifact validator.

## Migration map

The 13 existing Skill directories are renamed mechanically from `jimeng-*` to `dreamina-*`; `dreamina-cli` remains canonical. Directory name, frontmatter name, links, examples, READMEs, GitHub paths, and install commands change together. A validator rejects any remaining `jimeng-` identity while allowing product copy that explains the former name.

## Contracts

- `CapabilitySnapshot`: CLI version/commit plus parsed current help/schema.
- `GenerationRequest`: mode, prompt, references, model token, resolution, ratio, duration, count.
- `ApprovalReceipt`: exact request fingerprint and quote/credit acknowledgement.
- `OperationReceipt`: session, submit ID, state, timestamps, required action.
- `ArtifactReceipt`: local path, checksum, media metadata, source submit ID.

## Testing

Use versioned help/schema snapshots from an installed CLI without generation. Synthetic fixtures cover auth, permission, upgrade, validation, querying, failure, cancellation and downloads. A credit-consuming canary is never part of ordinary CI.
