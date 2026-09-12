# Closing paths — end-to-end verification

Two closing paths exist for the plan's final two gate lines, which are
disjunctions:

```text
read_only_runtime_contract = observed or explicitly blocked
paid_canary = separately approved or NOT_RUN
```

Both paths were dry-run end to end in a scratch copy of the repository,
with a **fake `dreamina` CLI** standing in for the real one. This proves
each path terminates correctly, so the owner's effort is not spent
against a broken tool. The scratch copy was used precisely so the real
repository's `docs/verification/` stays free of fabricated artifacts.

## Path (a) — accept the `explicitly blocked` / `NOT_RUN` branch

```text
$ python3 scripts/validate_distribution_v7.py --strict              -> 0
$ python3 scripts/validate_distribution_v7.py --require-runtime-gates -> 5

$ python3 scripts/record_gate_decision.py accept-blocked \
      --approver wandl --reason "dreamina CLI unavailable; live evidence deferred"
recorded docs/verification/gate-decision-accepted.md

$ python3 scripts/validate_distribution_v7.py --plan-gate            -> 0
$ python3 scripts/validate_distribution_v7.py --require-runtime-gates -> 5   # correct: (a) does not claim observed
```

Audit rows after recording:

```text
| `read_only_runtime_contract observed or blocked` | ✅ owner-accepted | satisfied via the **explicitly blocked** disjunct; docs/verification/gate-decision-accepted.md |
| `paid_canary = separately approved or NOT_RUN`  | ✅ owner-accepted | satisfied via the **NOT_RUN** disjunct; docs/verification/gate-decision-accepted.md |
```

Refusal behaviour confirmed: `--approver model` is rejected with
`REFUSED: approver 'model' is a placeholder` (exit 3), and nothing is
written.

## Path (b) — upgrade to `observed` / `APPROVED`

```text
$ # (fake) dreamina on PATH
$ python3 scripts/unlock_runtime_gates.py probe
{
  "help_bytes": 151,
  "schema_bytes": 121,
  "version": "dreamina 1.4.18"
}
# wrote cli-version.txt, cli-help.txt, cli-schema.json
# wrote account-readiness.md marked PENDING

$ python3 scripts/unlock_runtime_gates.py record-canary \
      --submit-id 20260912-a1b2c3d4-e5f6 --approver wandl \
      --observed "One 1k image generated; submit_id returned immediately; cost 1 credit; artifact downloaded and checksum verified."
wrote docs/verification/paid-canary-approved.md

$ python3 scripts/validate_distribution_v7.py                       -> observable state
  read_only_runtime_contract: observed
  paid_canary: APPROVED
  cli_available: True

$ python3 scripts/validate_distribution_v7.py --require-runtime-gates -> 0   # the goal
```

`--plan-gate` in that state:

```text
[x] read_only_runtime_contract = observed or explicitly blocked
    <- read_only_runtime_contract = observed (the 'observed' disjunct)
[x] paid_canary = separately approved or NOT_RUN  <- paid_canary = APPROVED
plan_gate = SATISFIED
```

## Refusals (the anti-fabrication boundary)

Verified in the same runs:

* `probe` with no `dreamina` on PATH → `BLOCKED`, exit 2, **no files
  written** (checked: `docs/verification/` unchanged).
* `record-canary --submit-id TBD` → `REFUSED: submit_id looks like a
  placeholder`, exit 3.
* `record-canary` missing any of `submit_id` / `approver` / `observed` →
  refused.
* `accept-blocked --approver model` → `REFUSED: approver 'model' is a
  placeholder`, exit 3.
* A second `accept-blocked` → refused (idempotence guard).
* Empty / malformed captures or a canary marker lacking required evidence
  → gate stays `blocked` / `NOT_RUN` (10 dedicated tests).

## Real repository state after these runs

```text
$ ls docs/verification/ | grep -E "cli-|account-|paid-canary|gate-decision"
(none)
```

The real repository contains **no** cli-version.txt, cli-help.txt,
cli-schema.json, account-readiness.md, paid-canary-approved.md, or
gate-decision-accepted.md. Every artifact produced during these
walkthroughs lived in `/tmp/path-ab-test` and `/tmp/path-b-test`. Nothing
was fabricated into the repository and no gate status changed.

## Exit-code contract, summarised

| Mode | Meaning | Path (a) result | Path (b) result |
|------|---------|:---------------:|:---------------:|
| (default) | structural checks only | 0 | 0 |
| `--strict` | all offline-verifiable gates | 0 | 0 |
| `--plan-gate` | the plan's 13 lines as written | **0** | **0** |
| `--require-runtime-gates` | demands `observed` + `APPROVED` | 5 (by design) | **0** |

`--plan-gate` matches path (a); `--require-runtime-gates` matches path (b).
They are not interchangeable.
