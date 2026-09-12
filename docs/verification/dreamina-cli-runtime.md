# `dreamina` CLI runtime evidence

> **Status (offline):** `read_only_runtime_contract = blocked`
> **Paid canary:** `paid_canary = NOT_RUN`
> **Snapshot parity:** `skill_snapshot_parity = PASS` (pinned to upstream HEAD `373bf7ffa698eefd3308576300e28ea2bff9cc6b`)
> **Strict TRACE:** `skill_trace = PASS` (13/13 upstream Skills PASS; see `docs/verification/skill-trace.md`)
>
> The blocked / NOT_RUN statuses are explicit. The verifier never silently
> passes any gate that depends on a real `dreamina` binary. Snapshot parity
> is now PASS because the upstream `full-aigc-skills/dreamina-skills`
> repository has been cloned locally and every packaged Skill's
> `upstream_commit_sha` matches the cloned upstream HEAD.

This document records the read-only CLI runtime evidence for
`codex-dreamina-design`. The read-only runtime evidence below is
currently empty: the local machine does not have the `dreamina` binary
installed, and the plugin has not been authorized to authenticate
against any user account. Paid operations remain explicitly out of
scope.

## 1. Unlocking the read-only runtime contract

To promote `read_only_runtime_contract` from `blocked` to `observed`:

1. Install the `dreamina` CLI on `PATH` (or pass an explicit
   `--dreamina-command` / `dreamina_command=` to the verifier).
2. Authorize the install with the user's Dreamina account using the
   official `dreamina login` flow (or whatever the current CLI
   recommends).
3. Re-run the v7 verifier:

   ```text
   $ python3 scripts/validate_distribution_v7.py
   ```

   The `read_only_runtime_contract` field should now read `observed`
   with a populated `read_only_runtime_reason`.

4. Capture the version/help/schema snapshot without performing any
   generation. Suggested invocation (argv-only, no paid action):

   ```text
   $ dreamina --version > docs/verification/cli-version.txt
   $ dreamina --help > docs/verification/cli-help.txt
   $ dreamina schema > docs/verification/cli-schema.json
   ```

   These outputs feed
   `scripts/dreamina_adapter.DreaminaAdapter.capability_snapshot()`
   and the per-mode validators in `image_service.py` /
   `video_service.py`.

5. Commit the evidence under `docs/verification/` in a separate change
   so it is reviewable.

## 2. Unlocking the paid canary

A paid image or video generation consumes membership benefits or
credits. The plugin plan explicitly forbids running such a canary as
part of CI. To record an action-time approval:

1. Run the action you intend to verify (image / video / multimodal)
   interactively via the `dreamina` CLI with a low-cost prompt.
2. Capture the submit ID, output URL, and observed cost.
3. Drop a marker at `docs/verification/paid-canary-approved.md` with
   the submit ID, the timestamp, the user/principal who approved it,
   and a one-paragraph summary of the observed behavior.

The v7 verifier detects the marker and reports `paid_canary = APPROVED`.
If the marker is absent, `paid_canary = NOT_RUN`. CI must never exit
non-zero purely because the canary is absent.

## 3. Skill snapshot parity — current status

The snapshot parity gate has been promoted to **PASS** by cloning the
upstream repository locally and pinning every packaged Skill to the
verified upstream commit.

**Pinned commit:** `373bf7ffa698eefd3308576300e28ea2bff9cc6b`

**Procedure (already executed):**

1. Clone the upstream repository:

   ```text
   $ git clone https://github.com/full-aigc-skills/dreamina-skills \
           /Users/wandl/workspaces/workspace-partme-ai/dreamina-skills
   $ cd /Users/wandl/workspaces/workspace-partme-ai/dreamina-skills \
       && git rev-parse HEAD
   373bf7ffa698eefd3308576300e28ea2bff9cc6b
   ```

2. Pin every packaged Skill to the verified commit:

   ```text
   $ python3 scripts/pin_upstream_sha.py \
           373bf7ffa698eefd3308576300e28ea2bff9cc6b skills
   pinned upstream_commit_sha=373bf7ffa698eefd3308576300e28ea2bff9cc6b in 13 Skills
   ```

3. Re-run the snapshot verifier with the upstream path:

   ```text
   $ python3 scripts/verify_skill_snapshot.py \
           --upstream-root /Users/wandl/workspaces/workspace-partme-ai/dreamina-skills \
           --strict
   ```

   The report's `parity_status` reads `PASS` because every packaged
   Skill's `upstream_commit_sha` matches the cloned upstream HEAD.

**Parity semantics:** the plan explicitly forbids copying upstream
Skill bodies into the plugin. Parity is therefore defined as "every
Skill correctly pins the verified upstream commit", not "every Skill
body byte-matches the upstream file". The snapshot verifier reads the
upstream HEAD directly from `.git/HEAD` to confirm the pin.

## 4. Boundary reminders

* The plugin **never** installs the `dreamina` binary on the user's
  behalf.
* The plugin **never** logs in to any user account.
* The plugin **never** performs generation as part of CI.
* The plugin **never** silently passes any runtime gate that requires
  the binary or upstream source.

If any of the above becomes necessary, update this document with a
dated rationale and a separate explicit authorization before
proceeding.
