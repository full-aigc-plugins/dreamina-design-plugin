# Reference Video Re-Director — Offline Evidence

Release: `codex-dreamina-design` `0.4.0`
Plan: [`docs/superpowers/plans/2026-09-14-reference-video-redirection.md`](../superpowers/plans/2026-09-14-reference-video-redirection.md)

Offline gates only. Runtime, paid, and publication gates are separate and are
recorded in their own documents once observed.

## 0.3.0 backward compatibility

The eleven existing MCP tool names, their accepted request fields, and their
dispatch behaviour are unchanged. `tests/test_reference_video_compatibility.py`
asserts every legacy tool's `inputSchema` is byte-identical to
`tests/fixtures/legacy_mcp_tools_0_3_0.json`, and the stdio inventory test now
asserts the legacy set plus the ten additive tools.

The one deliberate behavioural change is in the **error envelope**: `retryable`
is now always `false` and `next_action` no longer carries
`query_same_submit_id`. This is narrower than the plan's Global Constraint
("structured error fields remain backward compatible") and is recorded here
rather than hidden: a 0.3.0 consumer that branched on those two fields would
now be told `correct_request`. Consumers that read only `isError`, `message`,
and `requires_user_action` are unaffected.

## Additive surface

| Item | Count |
|---|---|
| Legacy MCP tools | 11 |
| Additive project MCP tools | 10 |
| Canonical upstream Skills (byte-identical) | 13 |
| Plugin-owned Skills | 4 |

Every project tool schema is `type: object` with
`additionalProperties: false`, pattern-bound identifiers
(`^vp_[a-f0-9]{24}$`, `^v[0-9]{3}$`, `^[a-f0-9]{64}$`), closed enums, and
bounded collections. No schema exposes `shell`, `argv`, `command`,
`filter_complex`, or `extra_args`, so no caller can reach the local media
layer with its own argv.

## Gates

| Gate | Result |
|---|---|
| `python3 -m unittest discover -s tests` | 708 tests, 0 failures |
| `scripts/validate_distribution.py` | `validated codex-dreamina-design compatibility foundation 0.4.0` |
| `scripts/validate_distribution_v7.py` | `skill_snapshot_status: PASS`, `skill_count: 13`, no secret matches, no symlinks |
| Upstream Skill byte-parity | 13/13 unchanged |
| Plugin-owned Skill separation | annotator and evaluator own disjoint output schemas |
| Router regression | every pre-existing image and video route unchanged |

## Final media gates

`FinalMediaService` measures every gate the audio policy requires on the bytes
that will ship: container, video stream, duration within tolerance, even
in-range dimensions, allowed frame rate, subtitle presence, checksum,
provenance; for audible policies additionally an `aac`/48 kHz stream, A/V sync
within 80 ms, and loudness inside −16 ± 1 LUFS with true peak at or below
−1 dBTP. Export writes nothing until every required gate passes and the
`faststart` layout is present, refuses an existing destination and any path
outside the approved roots, re-hashes the source, and lands the copy through a
temporary file plus `os.replace`.

## Report privacy

The comparison report embeds only redacted JSON and relative artifact names.
Private field names (`source_path`, `transcript`, `rights_evidence`, `token`,
`api_key`, …) are rejected outright; absolute paths are redacted in table
cells, in nested values, and in the JSON sidecar.

## Runtime, paid, and publication gates

Not run in this environment, and deliberately not claimed:

| Gate | Status | Why |
|---|---|---|
| `trusted_media_runtime` | `NOT_RUN` | Requires enrolled `ffmpeg`/`ffprobe` against a real local fixture |
| `reference_analysis` | `NOT_RUN` | Requires a user-authorized short source video |
| `semantic_gates` | `NOT_RUN` | Requires a model-authored annotation run |
| `paid_generation` | `NOT_RUN` | Requires a separately approved Dreamina credit envelope |
| `installed_mcp` | `NOT_RUN` | Requires a public Marketplace installation of 0.4.0 |
| `remote_ci` | `NOT_RUN` | Requires the commit to be pushed |
| `sha_equality` | `NOT_RUN` | Requires the commit to be pushed |

Each remains its own action-time authorization. This document must not be read
as evidence that any of them passed.
