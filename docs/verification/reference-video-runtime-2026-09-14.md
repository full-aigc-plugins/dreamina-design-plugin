# Reference Video Re-Director — Runtime Acceptance Evidence

Release: `codex-dreamina-design` `0.4.0`
Plan: [`docs/superpowers/plans/2026-09-14-reference-video-redirection.md`](../superpowers/plans/2026-09-14-reference-video-redirection.md) Task 17
Runner: `scripts/run_reference_video_acceptance.py`

Command actually executed:

```bash
python3 scripts/run_reference_video_acceptance.py --no-paid
```

## Observed gate output

| Gate | Status | Reason |
|---|---|---|
| `offline_suite` | **PASS** | 747 tests, `OK` |
| `trusted_media_runtime` | `NOT_RUN` | Trusted media tools present but no authorized source fixture was processed |
| `reference_analysis` | `NOT_RUN` | Requires a project run against a user-authorized source fixture |
| `semantic_gates` | `NOT_RUN` | Same |
| `rights_and_redesign` | `NOT_RUN` | Same |
| `batch_quote` | `NOT_RUN` | Same |
| `paid_generation` | `NOT_RUN` | Paid generation was not authorized for this run |
| `shot_evaluation` | `NOT_RUN` | Same |
| `final_composition` | `NOT_RUN` | Same |
| `audio_subtitles` | `NOT_RUN` | Requires a project run against a user-authorized source fixture |
| `installed_mcp` | `NOT_RUN` | Publication was not authorized for this run |
| `remote_ci` | `NOT_RUN` | Same |
| `sha_equality` | **PASS** | `branch=feat/reference-video-redirection-0.4.0 head=cbe4ec5f49b2 tracking=cbe4ec5f49b2 remote=cbe4ec5f49b2` |

`all_passed = false`. Two of thirteen gates passed; the other eleven are
`NOT_RUN` with a recorded reason. **No runtime, paid, or publication gate is
claimed.**

## Why the runtime gates are NOT_RUN

They are blocked on authorization and fixtures, not on missing code:

- **`trusted_media_runtime` / `reference_analysis` and the project gates** need
  a user-authorized short local source video. Enrollment and source processing
  additionally raise native confirmation dialogs, which require a person at the
  machine. Tool presence alone is deliberately *not* treated as a measurement.
- **`paid_generation` / `shot_evaluation` / `final_composition`** need a fresh,
  exact credit envelope for this run. The plan forbids reusing the earlier 56- or
  98-credit approvals, and the runner enforces that: it fails closed, rejects a
  consumed approval as stale, and rejects any approval id that is not authorized
  for the current run.
- **`installed_mcp`** needs a public Marketplace installation of `0.4.0`.
- **`remote_ci`** cannot run for this branch at all: `.github/workflows/ci.yml`
  triggers on pushes to `main` and on pull requests only, so a push to
  `feat/reference-video-redirection-0.4.0` produces no run. This gate becomes
  reachable only through a pull request or a merge to `main`.

## Anti-false-pass properties proven by the test suite

`tests/test_run_reference_video_acceptance.py` (25 tests) pins that the runner
cannot overstate its own result:

- every gate starts `NOT_RUN`, and every `NOT_RUN` gate carries a non-empty reason;
- a gate becomes `PASS` only from output the runner observed, never from a
  tool being present or a command being available;
- authorizing a paid run does **not** produce a paid `PASS`;
- authorizing publication does **not** produce an installed `PASS`;
- an approval consumed by an earlier batch raises `StaleAuthorizationError`;
- an approval that does not authorize the current run raises
  `AcceptanceAuthorizationError`;
- a CI run that is still queued is not reported as passing.

The runner also refuses to nest inside its own offline suite: a test that
invokes the CLI would otherwise re-run the suite containing that test. The child
sees `DREAMINA_ACCEPTANCE_INSIDE_SUITE` and reports the gate as `BLOCKED` rather
than recursing.

## Reproducing

```bash
python3 scripts/run_reference_video_acceptance.py --no-paid --json
```

This spends nothing, touches no network, and changes no repository state.

## Prerequisites for the remaining gates

| Gate | Requires |
|---|---|
| runtime and project gates | an operator-supplied, authorized source fixture plus native dialogs |
| paid gates | a fresh credit envelope with the exact quote, attempt count, fingerprints, and output path |
| `installed_mcp` | authorized publication, then installation of `0.4.0` |
| `remote_ci` | a pull request, or merging to `main` |

Each is its own action-time authorization. This document records what was
observed and nothing more.
