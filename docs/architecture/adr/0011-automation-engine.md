# ADR-0011: Automation engine

**Status:** Accepted (Phase 5, 17 Nov 2026)

## Context

The brief asks for automations triggered by Minecraft events or schedules, configured through the management
console, which Claude can help write. Each automation needs an owner, trigger, conditions, permissions, action,
state, schedule and timezone, history, error policy, retries, timeouts and resource and AI limits. They must
survive restarts without running twice, and must not loop, duplicate events or call the model too often.
AI-written and privileged automations need approval, and custom code must be isolated and treated as privileged.

## Decision

**It runs inside the gateway.** The gateway already holds the state (SQLite), the identities and roles, the
audit log, the event stream to the mod and the AI job queue. A separate container would need its own copy of all
of that, plus another token and API. The engine is one module (`src/mcai/automation.py`) with its own scheduler
thread and a small worker pool. The empty `containers/automation` placeholder from Phase 0 is removed.

**Definitions are data, not code.** An automation is a TOML or JSON document (`automation-examples/`): a trigger,
conditions, 1–10 actions and limits. It is validated strictly: unknown keys, unknown placeholders and out-of-range
values are rejected. Action types come from a fixed list (message, broadcast, notify_role, log, status_report,
save, world, set_role, ai_message, ai_build). Each type needs a named permission and runs code written and reviewed
in this repository. There is no "run a command" action and no scripting. A new kind of action is a code change,
reviewed and deployed like any other, which makes custom code a privileged, administrative operation by design.

**Ownership and permissions.** The creator becomes the owner (`account:<name>`) and must hold every permission the
actions need. The engine checks the owner's current role **at every run**; if the owner was demoted or disabled,
the run is denied and the automation suspended. An automation never gives more than the builder role and never
lowers one.

**Approval.**

- Automations written by the AI land as `proposed`. They never run until approved.
- *Privileged* automations change roles (`set_role`) or the world (`ai_build`). Unless the owner created them,
  they land as `pending_approval`, and only the owner (`automations.approve`) can approve them. AI proposals that
  are privileged need the owner too.
- AI proposals use Claude Code with no tools at all. The answer is parsed as JSON and validated exactly like a
  hand-written file. An invalid proposal is recorded as a failed request with the reason.

**Triggers.**

- *Schedules*: 5-field cron in an IANA timezone, with DST handled (a skipped time doesn't run that day, a repeated
  time runs once), or intervals aligned to the epoch so restarts compute the same slots.
- *Events*: player join, first join and leave, area enter and leave, advancement, `/ai trigger <name>`; build
  completed, failed and undone; server started and thresholds (mspt, TPS, players online, heap %); world day and
  night start, rain start and stop.
- The mod reports areas, advancements (one mixin on `PlayerAdvancementTracker.grantCriterion`) and triggers to
  `POST /v1/mod/game-events`. World time and weather ride on the 30-second heartbeat, whose reply carries the areas
  to watch.

**Exactly-once where it matters, at-most-once otherwise.**

- Every run has an idempotency key (`sched:<id>:<slot>`, `evt:<id>:<session id | event id | ...>`) under a UNIQUE
  index, so a slot or event is never run twice, also after a crash between creating the run and moving the
  schedule on.
- A run that was executing when the gateway stopped becomes `interrupted` and is not repeated: actions such as
  role changes or broadcasts must not happen twice.
- Retries resume at the failed action; finished actions of that run are not repeated.
- Slots missed while the gateway was down run once if less than `misfire_grace_s` late (default 300 s), otherwise
  per `misfire` (`skip` by default, or `run` once).

**Guards.**

- *Loops*: events caused by an automation (today, builds it requested) carry a chain of automation ids. A chain that
  would reach the same automation again, or that is deeper than 3, is refused and audited.
- *Rates*: `max_runs_per_hour` and `cooldown_s` (per player) for each automation; `AUTOMATION_MAX_RUNS_PER_MINUTE`
  across all of them.
- *AI*: per automation per UTC day, USD and token caps (`ai_max_usd_per_day` must be set above 0 to use AI at
  all). Across automations, `AUTOMATION_AI_CALLS_PER_HOUR`. The shared daily AI budget and the AI on/off switch
  also apply, and each call is capped by its own `max_usd`.
- *Failures*: `on_error` is stop, continue or disable. After 5 failed runs in a row the automation is suspended.
- *Timeouts* are checked between actions; an AI action waits at most until the run's deadline.

**Interfaces.** `mcai automations …` (alias `auto`): list, show, runs, export, validate, import (local file sent
over stdin), install an example, enable/disable, approve/reject, run now, delete, and propose (ask the AI).
`mcai tui` gets an Automations panel. Everything is audited: changes, approvals, every run's result, guard trips
and misfires.

## Consequences

- No new container, port, secret or Docker privilege. Automations can't do anything the gateway couldn't already
  do, and only through the same allowlisted paths (mod events, RCON `save-all`/`time`/`weather`, role table).
- Full backups stay with the backup service (`BACKUP_INTERVAL`). Automations can only flush the world (`save`):
  starting backups would need the Docker socket, which nothing in the platform holds.
- Areas are checked twice a second in the mod with at most 200 boxes, which is cheap. Advancement events need the
  mixin, so a Minecraft update can require a mod update (the build fails loudly rather than silently).
- At-most-once for interrupted runs means a crash can skip an action; this is preferred over doing it twice.
