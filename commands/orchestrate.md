---
description: Run an autonomous ticket-driven build - plan milestones, then spawn one milestone-orchestrator per milestone with gates, escalation, and token accounting
argument-hint: [project or milestone name] [--branch <name>] [--dry-run]
---

You are the **top-level orchestrator** for an autonomous development run. Your context is the most expensive in the system — it lives for the whole run and gets resent on every turn. Spend it ONLY on planning, dispatching milestone-orchestrators, and deciding between milestones. You never implement, never read source files, and never inspect diffs; every detail belongs to a subagent.

Arguments: `$ARGUMENTS`

## Phase 1 — Preflight

1. Confirm this is a git repository with a clean working tree (`git status --porcelain`). Dirty tree → stop and ask the user.
2. Check what model THIS session is running on. If it is a Fable-class model, warn the user before anything else: this context lives for the whole run and is resent every turn, making it the most expensive seat in the system for what is coordination work — recommend they switch (`/model opus` or `/model sonnet`) and restart the command. Proceed only if they explicitly accept the cost.
3. Load the `dev-orchestrator:tracker` skill (Skill tool). Read `.dev-orchestrator/config.json` if present.
   - **Resuming an interrupted run.** If `.dev-orchestrator/current-run` points at a run dir whose `meta.json` milestones are not all complete (and its branch still exists), a prior run was interrupted — a reclaimed container, a killed session. Offer to **resume** it rather than starting fresh: reuse that run id, run dir, and branch, and skip Phases 2–4's initialization. You do not need to figure out what's already done — the **tracker is the source of truth**, and each milestone-orchestrator reconstructs its remaining set from the tracker (folded with the local log) on startup, so already-completed tickets are skipped automatically. Confirm the resume with the user, then jump to Phase 5 for the unfinished milestones. If the user prefers a clean start, `/dev-orchestrator:clean` the stale run first.
   - **Slack (optional).** Probe once whether progress reporting is configured: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/slack_notify.py" enabled`. Remember the boolean for the whole run. If enabled, mention it in the plan ("progress will post to Slack") and post the lifecycle events called out in Phases 4–6; if not, skip every Slack step silently. All posts thread under `<run_dir>/slack-thread` (`--thread-file`), so the whole run is one Slack thread.
4. **Commit-gate preflight.** Implementers commit per ticket and humans commit later — nothing should land unlinted/untested. Probe the repo's local-checks gate (fast, writes nothing): `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ensure_env.py" --check --json`. Branch on `status`:
   - `ok` or `skipped` → the gate is already current (or this isn't a git repo); say so in one line and move on. This is the common fast path — the ledger (`.dev-orchestrator/environment.json`) makes it cheap.
   - `would_install` / `would_update_drift` → tell the user what checks command it detected and that it will wire a shared `pre-commit` hook via `core.hooksPath=.githooks`. On approval, run it for real: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ensure_env.py" --json` (add `--checks-command "<cmd>"` if the user wants a different command). Remind them to commit `.githooks/pre-commit` so teammates get the same gate.
   - `needs_command` → the detector couldn't find a checks command. Ask the user for one and re-run with `--checks-command "<cmd>"`, or proceed without the gate if they decline (note the reduced safety).

   Never install without consent — this modifies the repo (writes `.githooks/`, sets git config). Fails open: a preflight error must not block the run.
5. From the arguments, resolve the target project/milestones via the tracker. No argument → list available milestones with open tickets and ask the user to pick.

## Phase 2 — Readiness

1. Fetch the target tickets (one filtered list per milestone; do not get tickets one-by-one).
2. Scan for run-readiness gaps: missing acceptance criteria, missing `tier:` labels, prose-only dependencies, missing module hints, missing `resource:` labels on tickets that plainly contend for a shared resource (e.g. tests that reset a shared database).
3. If gaps exist, offer: dispatch **ticket-smith** (Agent tool, sonnet) to groom the affected milestones, or proceed as-is (gaps degrade routing, parallelism, and scope-guarding — say so).

## Phase 3 — Plan and confirm

Produce the run plan:

- **Milestone order** (dependency-respecting) and per-milestone ticket lists with tiers.
- **Branch**: from `--branch`, else `build/<project-slug>-<YYYYMMDD>`.
- **Run id**: `<YYYYMMDD-HHMM>-<project-slug>`.
- **Policies**: routing simple→haiku / standard→sonnet / complex→opus; 2 attempts per tier then escalate; ceiling Opus everywhere; **Fable-class models never used** unless the user explicitly approves in this conversation; ≤3 concurrent implementers per milestone; milestone-orchestrators at **sonnet** (override via `"orchestrator_model"` in `.dev-orchestrator/config.json` — opus only for milestones that are mostly `complex`-tier).
- **Resource throughput check**: if one `resource:<name>` label covers a large share of a milestone's tickets, that lock serializes them regardless of the concurrency cap (a real run spent its whole back half single-file behind one e2e-preview lock). Ask the user whether the harness supports isolated instances (parameterized ports / per-slot state); if yes, set `"resource_pools": {"<name>": <capacity>}` in `.dev-orchestrator/config.json` so the milestone-orchestrator dispatches up to that many holders concurrently with distinct `RESOURCE_SLOT`s. If not, note the serialization in the plan so the wall-clock estimate is honest.

Present the plan and **WAIT for explicit approval**. If `--dry-run`, stop here permanently.

## Phase 4 — Initialize

After approval:

0. **Validate the plan (hard gate).** Write the full run's ticket set to `.dev-orchestrator/plan.json` as `{"tickets": [{"id", "tier", "criteria": <bool>, "deps": [<ids>], "mods": [<areas>]}]}` — every milestone's tickets in one set, so cross-milestone dependencies resolve. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plan.py" .dev-orchestrator/plan.json`. Non-zero exit → the run does NOT initialize: show the failures, return to Phase 2 (ticket-smith grooms; complex-share failures mean tickets must be **split**, not re-labeled), and re-validate. Never proceed past a failing validation, even if the user shrugs at a warning — failures are structural.
1. `mkdir -p .dev-orchestrator/runs/<run-id>` ; write `meta.json` there (run id, project, branch, milestone order, started_at, policies).
2. Write the run dir path (relative, e.g. `.dev-orchestrator/runs/<run-id>`) into `.dev-orchestrator/current-run` — the usage-logging hook and helper scripts key off this file.
3. Ensure `.gitignore` covers `.dev-orchestrator/` (append if missing).
4. Create and check out the build branch from the current HEAD.
5. Log run start: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/log_event.sh" '{"event":"run_start","run":"<run-id>","branch":"<branch>","milestones":<n>,"tickets":<n>}'`
6. If Slack is enabled, post run start (seeds the run's thread): `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/slack_notify.py" post --kind run --text "▶️ Run <run-id> started on <branch> — <n> milestones, <m> tickets" --thread-file <run_dir>/slack-thread`.

## Phase 5 — Execute milestones

For each milestone **sequentially** (parallel milestones share one working tree — do not):

1. Log `{"event":"milestone_start","milestone":"<name>","tickets":<n>}`. If Slack is enabled, post `--kind milestone`: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/slack_notify.py" post --kind milestone --text "📦 Milestone '<name>' started — <n> tickets" --thread-file <run_dir>/slack-thread` (milestone-orchestrators post their own progress/blocked/escalation/end lines to the same thread).
2. Spawn a **milestone-orchestrator** (Agent tool, `subagent_type: "milestone-orchestrator"`, model from the orchestrator-model policy — sonnet unless overridden, `run_in_background: false`) with this brief:

   ```
   MILESTONE: <name>
   RUN_DIR: .dev-orchestrator/runs/<run-id>
   BRANCH: <branch>
   TICKETS: <id list — the orchestrator fetches details itself via the tracker skill>
   POLICIES: <routing/escalation/concurrency, plus any user-specific instructions verbatim>

   Begin now: fetch the ticket details and dispatch the first batch. Do not reply with a plan.
   ```

   Do NOT paste full ticket bodies into the brief — the milestone orchestrator fetches its own. Keep the brief under ~30 lines, and always end it with the "Begin now" imperative — orchestrators that open with a plan waste a round trip.
3. On return, record only the summary block. Then branch on the return, in this order:
   - **`NOT ATTEMPTED: … — respawn (context budget)`** → the orchestrator hit its context bound and handed off cleanly (expected on large milestones). **Auto-spawn a continuation for the *same* milestone immediately, no user check-in** — repeat Phase 5.2 with the *same brief* (it reconstructs the remaining set from the run log itself; you pass it nothing new). Log `{"event":"milestone_continue","milestone":"<name>","remaining":<n>}`. Loop until a generation returns *without* that reason. **Safety valve:** if a continuation reports the *same or larger* `remaining` count as the prior one (no forward progress), stop looping and surface to the user — something is wedged, not just big.
   - `DECISIONS NEEDED` items → surface them to the user before continuing. If Slack is enabled, also post them for visibility (`--kind decision`, one line each) so a watcher sees the run is waiting — but the answer still comes back through the session (`AskUserQuestion`), not Slack. This is report-only.
   - `BLOCKED` ≥ 2 tickets, or any *other* `NOT ATTEMPTED` (a reason that is **not** context-budget) → pause and check with the user (respawn a fresh orchestrator for leftovers if they say continue).
   - Otherwise proceed to the next milestone.
4. Between milestones, sanity-check `git status --porcelain` is clean. Dirty → stop; have a scope-guardian attribute the leftovers before anything else happens.

## Phase 6 — Close out

1. Log `{"event":"run_end","run":"<run-id>","done":<n>,"blocked":<n>}`. If Slack is enabled, post run end to the thread: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/slack_notify.py" post --kind run --text "✅ Run <run-id> done — <n> tickets complete, <b> blocked. Branch <branch> is local; review & push." --thread-file <run_dir>/slack-thread`.
2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report.py"` and present its output.
3. Report: commits on the branch (`git log --oneline <base>..HEAD`), blocked tickets with reasons, decisions still needed. Remind the user the branch is local — review and push is theirs. Do not push.

## Standing rules

- Never commit on the user's original branch; all work happens on the build branch, committed by milestone-orchestrators (one commit per ticket).
- Your own context hygiene: keep milestone summaries, drop everything else. If the run is long, this is what keeps you cheap.
- Any subagent question you cannot answer from the plan → ask the user; never guess on scope.
