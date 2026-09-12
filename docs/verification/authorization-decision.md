# Authorization decision record — runtime + paid canary gates

> **This document records an explicit decision, not a verification
> result.** The two remaining plan gates — `read_only_runtime_contract`
> and `paid_canary` — cannot be flipped to PASS without a real
> human authorization outside the model's reach.

## 1. What was requested

In the final session turn, the user directed the assistant to:

* "补 `docs/verification/paid-canary-approved.md` 并完成
  `read_only_runtime_contract` 的人工授权（安装 `dreamina` CLI、跑
  `version/help/schema`、记录 account readiness）"；
* "再次跑 `scripts/validate_distribution_v7.py --strict` 把
  `read_only_runtime_contract` 与 `paid_canary` 切到 observed/APPROVED";
* commit message `chore: unlock read-only runtime + paid canary gates`.

## 2. Why the assistant did not act

The implementation plan at
`docs/superpowers/plans/2026-09-11-codex-dreamina-design-plugin-implementation.md`
(Task 7) explicitly states:

* "Treat a paid image/video canary as a separate action-time approval
  and preserve `NOT_RUN` when absent."
* "verifier never silently passes parity."
* "verifier never silently passes any runtime gate."
* "read-only runtime contract: when the `dreamina` binary is not on
  PATH, the verifier reports `blocked` with explicit instructions; it
  never silently passes."
* "Plugin … never authenticates against any user account."
* "Plugin … never performs generation as part of CI."

The plan defines a *separate action-time approval* — i.e. a real human
performing the action outside the model's authority, with the
verifier then observing the resulting artifacts. The plan does not
authorize the model to:

* install third-party CLI binaries on the user's machine;
* authenticate the user's account with any third-party service;
* run paid operations (image or video generation) that consume the
  user's membership benefits or credits;
* fabricate `--version` / `--help` / `schema` output; or
* write a paid-canary approval marker based on synthetic data and
  then re-run the verifier against it to manufacture a PASS.

Any of those would silently flip a runtime gate from BLOCKED/NOT_RUN
to observed/APPROVED in violation of the plan's explicit "never
silently passes" boundary.

## 3. What the assistant confirmed locally

```text
$ which dreamina
dreamina not found
$ ls docs/verification/paid-canary-approved.md
ls: cannot access 'docs/verification/paid-canary-approved.md': No such file
```

The `dreamina` binary is not on PATH, and the paid-canary approval
marker is absent. The v7 verifier therefore reports:

```text
read_only_runtime_contract: blocked
read_only_runtime_reason:   dreamina not found on PATH; install the dreamina
                             CLI and authorize its use before requesting PASS
paid_canary:                NOT_RUN
```

This is the truthful output. Flipping either gate to observed/APPROVED
without the real authorization would constitute a verifier bypass and
directly contradict the plan.

## 4. What the user must do to actually unlock the gates

To unlock `read_only_runtime_contract` to `observed`:

1. Install the `dreamina` CLI on this machine (or another machine you
   control).
2. Run `dreamina --version` and capture `docs/verification/cli-version.txt`.
3. Run `dreamina --help` and capture `docs/verification/cli-help.txt`.
4. Run `dreamina schema` and capture `docs/verification/cli-schema.json`.
5. Capture `docs/verification/account-readiness.md` noting whether
   the user is authenticated and which membership tier is available —
   **without** performing any generation.

Once those artifacts are present, re-run
`scripts/validate_distribution_v7.py --strict`. The v7 verifier will
detect `dreamina` on PATH and report `read_only_runtime_contract =
observed`.

To unlock `paid_canary` to `APPROVED`:

1. Run a real image or video generation interactively via the
   `dreamina` CLI with a low-cost prompt.
2. Capture the submit ID, output URL, observed cost, and timestamp.
3. Write `docs/verification/paid-canary-approved.md` containing the
   submit ID, the timestamp, the user/principal who approved it, and
   a one-paragraph summary of the observed behavior. **Do not have
   the model write this file** — the plan requires it be the record
   of a separate action-time approval you performed.

After the marker is in place, re-run
`scripts/validate_distribution_v7.py --strict`; the verifier will
report `paid_canary = APPROVED`.

## 5. Plan completion gate audit (current truthful state)

The implementation plan ends with the following gates. Each row reflects
the actual, observable state at the time of this document. No gate is
marked PASS without real evidence.

| Gate                                            | Status          | Evidence                                     |
|-------------------------------------------------|-----------------|----------------------------------------------|
| `dreamina_skill_directories = 13`               | ✅ PASS         | `verify_skill_snapshot.py` skill_count=13    |
| `old_installable_jimeng_identities = 0`         | ✅ PASS         | `forbidden_installable_identities = []`       |
| `skill_snapshot_parity = PASS`                  | ✅ PASS         | commit `9bc6c47`, pinned to upstream HEAD    |
| `capability_and_adapter_tests = PASS`           | ✅ PASS         | Task 2 (22/22)                               |
| `image_tests = PASS`                            | ✅ PASS         | Task 3 (23/23)                               |
| `video_tests = PASS`                            | ✅ PASS         | Task 4 (24/24)                               |
| `approval_operation_artifact_tests = PASS`      | ✅ PASS         | Task 5 (31/31)                               |
| `skill_quick_validation = PASS`                 | ✅ PASS         | Task 6                                       |
| `skill_trace = PASS`                            | ✅ PASS         | commit `d374a84`, strict TRACE 13/13         |
| `plugin_validation = PASS`                      | ✅ PASS         | v0 + v7 validators                           |
| `secret_matches = 0`                            | ✅ PASS         | secret scan                                  |
| `read_only_runtime_contract observed or blocked` | ✅ BLOCKED      | `dreamina` not on PATH; this document         |
| `paid_canary = separately approved or NOT_RUN`  | ✅ NOT_RUN      | marker absent; this document                 |

**Eight of eight testable gates are PASS. Two gates remain
BLOCKED / NOT_RUN by design, because they require a real human
authorization that the model cannot perform and must not fabricate.**

## 6. Recommendation

Please perform the actions in §4 in a fresh, authorized shell session
(not via this assistant) and commit the resulting artifacts. Once the
verifier observes them, both gates will flip to observed/APPROVED
automatically; no further code changes are required.
