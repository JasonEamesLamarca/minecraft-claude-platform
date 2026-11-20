# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may break).

## [Unreleased]

## [0.6.2] - 2026-11-21

First public pre-release.

### Security
- `scripts/restore.sh` could skip its check for unsafe paths (absolute or `..`) in a large backup archive: an
  early-exiting `grep -q` under `pipefail` made a match read as "nothing unsafe". GNU tar's own `..` stripping
  still kept files inside the data folder. The check now reads the whole listing.
- The AI worker has no route to the internet any more. Its only way out is the new `egress-proxy` service:
  HTTPS tunnels to the host names in `EGRESS_ALLOW` (default `api.anthropic.com`), never to IP addresses or
  private networks, each one logged.
- The AI worker image no longer contains npm, npx or corepack (Trivy found 10 HIGH CVEs, all in npm's own
  dependencies; Claude Code doesn't need them at runtime).

### Added
- CI: ruff lint, image builds for linux/amd64 and linux/arm64, Trivy image and configuration scans (fixable
  HIGH/CRITICAL findings fail the build), SPDX SBOMs per image.
- Release workflow (`v*` tags): mod jar, SBOMs, `SHA256SUMS` and build provenance attestations in a draft
  release; a manual run is a dry run.
- Docs: memory sizing (4G heap default, 6G for Atlantis Full) and how to change it; keep players away from an
  example install, or run `repair` afterwards.

### Fixed
- Work handed out on a long poll was lost if the poller had just gone away: a job sent to an AI worker that was
  stopping stayed "running" until its timeout (the player never got an answer), and events sent to a mod that
  was disconnecting were dropped. The gateway now queues such a job again and keeps such events for the next poll.
- Mod 0.6.1: the example placer read chunks that were requested but not yet loaded, so the server thread waited
  for them; in ungenerated terrain one wait passed 60 s and the watchdog stopped the server (Atlantis Full).
- Mod 0.6.2: every placed water block scheduled a fluid tick. Atlantis Full queued 7.5 million in one region
  file, lagging the server until it ran out of memory. Placed and restored pieces now drop their scheduled
  block and fluid updates, so a build stays as designed.
- The gateway's threads shared one SQLite connection without serializing reads; concurrent requests could fail
  with `InterfaceError` (seen when the mod downloads and reports at the same time). Every statement now runs
  under one lock (`test_concurrent_mod_calls`).
- `gateway`, `ai-worker` and `ssh-gateway` now run with `init: true`. Without a reaping PID 1, finished SSH
  sessions left zombies until the ssh-gateway's `pids_limit` (64) was reached and new sessions failed.

### Added
- Phase 6, example builds (ADR-0012):
  - **Atlantis**, an original underwater city by the project's own generator (`examples/atlantis/generator`,
    stdlib Python, 25 test files). Franchise-specific elements of the private original were replaced by new
    designs (Trident Arch with Plato's rings, manta skiffs, orichalcum Crystal Hall, navigation room with orrery).
  - **Packs**: deterministic Sponge v3 `.schem` sections with a manifest (SHA-256, offsets, places of interest).
    Atlantis Lite (a sealed 96 x 768 cutaway: tower, Fountain Plaza district, pier, dome wall, sea; 0.2 MB) and
    Atlantis Full (90 sections, 255 million blocks, 4.7 MB). `scripts/build-examples.sh` builds them and pins the
    hashes in `examples/catalog.json`.
  - **`mcai examples …`**: list, catalog, info, download (client-side, allowlisted GitHub release URLs, pinned
    hashes, `--from` for local builds), validate, delete, install (preview, then `--yes`), installs, status,
    repair, rollback, cancel, forget. Permissions `examples.read`, `examples.manage`, `examples.install`.
  - **Gateway**: strict manifest schema; bounded gzip/NBT parsing; deny-list; no block entities or entities;
    install checks for world height, protected areas, overlap and size; one job at a time; interruption handling.
  - **Mod 0.6.0**: `ExamplePlacer` places packs chunk by chunk with undo snapshots, changing only differing
    blocks (`EXAMPLES_BLOCKS_PER_TICK`), and restores them on rollback.
  - **Automations**: areas that follow an example's install, `example_tour` and (privileged) `example_restore`
    actions, and three Atlantis automations (arrival, tour, weekly restore).
- Phase 5, automations (ADR-0011):
  - **Engine in the gateway.** Declarative TOML/JSON definitions with a trigger, conditions, up to 10 actions from
    a fixed list (message, broadcast, notify_role, log, status_report, save, world, set_role, ai_message,
    ai_build) and limits (timeout, retries, error policy, runs per hour, cooldown, AI USD and tokens per day).
  - **Triggers.** Cron with IANA timezones (DST-safe) and intervals. Events: joins, first joins, leaves, areas,
    advancements, `/ai trigger <name>`, build results, server start, thresholds (mspt, TPS, players, heap) and
    world day, night and rain.
  - **Guarantees.** Idempotency keys (no duplicate runs, also across restarts); interrupted runs aren't repeated;
    retries resume at the failed action; loop and chain guards; global run and AI-call rates; suspension after
    repeated failures or when the owner loses a permission.
  - **Approval.** AI-written automations (`mcai automations propose`) and privileged ones (role changes, builds)
    wait for approval; privileged ones need the owner.
  - **Console.** `mcai automations …` (alias `auto`): list, show, runs, export, validate, import (local file),
    examples and install, enable and disable, approve and reject, run now, delete. `mcai tui` has an Automations
    panel (F7).
  - **Mod.** It reports area enter and leave, completed advancements (mixin), `/ai trigger` and world
    time/weather, and shows automation broadcasts.
  - **Examples.** 11 example automations in `automation-examples/`, all validated by the tests.
- Phase 4a, management and roles (ADR-0010):
  - **Roles.** Six roles (owner, admin, operator, builder, player, auditor). Players are identified by UUID; the
    owner comes from `MC_OWNER` at first join (online mode only). Assignment rules block escalation, and
    `scripts/set-role.sh` handles host-only changes.
  - **SSH administration.** An SSH gateway container (unprivileged OpenSSH, keys only, forced command, no
    PTY/forwarding/files, bound to 127.0.0.1 by default) and a cross-platform `mcai` CLI with pinned host keys.
    The CLI has 37 role-checked commands for status, players, whitelist, logs, restart, AI history, approvals and
    limits, audit and accounts.
  - **Audit.** Hash-chained daily audit files with retention, export and `mcai audit verify`. Player joins and
    leaves, sessions and server health (TPS, heap) are reported by the mod.
  - **Full-screen console.** `mcai tui` (Textual) replaces the shell-based `mc-admin` console.
  - **Smaller additions.** `/ai link <code>` links a player to a management account. Undo snapshots are pruned
    after an undo and after `AI_UNDO_KEEP_DAYS`. The setup wizard registers the owner's SSH public key and
    records the mod's checksum, so an upgraded jar restarts the server.

### Changed
- `/ai build` needs the builder role; `AI_BUILD_MIN_OP_LEVEL` is removed. The mod no longer sends op levels.
- Audit log moved from `audit/audit.jsonl` to daily `audit/audit-YYYY-MM-DD.jsonl` files (migrated automatically).

### Fixed
- A non-interactive re-run of `setup.sh` asked for the stored AI credential again instead of keeping it.

- Phase 3: `/ai build <description>` → Claude designs a plan with two MCP tools (no other tools) → gateway
  re-validates (denied blocks, no NBT, size/budget/distance limits, protected areas) → preview with particle
  outline → `/ai approve` → the mod places blocks per tick after saving a structure snapshot → `/ai undo`
  restores it. Also `/ai cancel` and `/ai history`. ADR-0009. Old platform-mod jars are removed on upgrade.
- Tests: 160 Python tests, 12 mod tests; operational test covers the build lifecycle and a rejected TNT plan.
- Phase 2: server-side Fabric mod `mcai` (`/ai help|ask|status`), integration gateway (Python stdlib; separate
  mod/worker tokens, rate/pending/queue/daily-budget limits, timeouts, audit log), AI worker running Claude Code
  2.1.316 with no tools and per-request budget/turn caps, plus a mock provider for CI. Single `secrets/ai_credential`
  with a terms notice for subscription tokens. ADR-0008. Structured JSON logs.
- Tests: 83 Python tests (unit + gateway/worker integration with mock AI), 7 mod tests, operational test now covers
  the `/ai` round trip and container isolation. CI jobs for the mod build and service images.
- Phase 1: `compose.yaml` with a hardened Fabric server (itzg/minecraft-server, pinned by digest, non-root,
  read-only root filesystem, all capabilities dropped, internal-only RCON via Docker secret, whitelist and
  online-mode on) and scheduled backups (itzg/mc-backup).
- `scripts/setup.sh` wizard (prerequisites, explicit EULA acceptance, owner, port, memory, generated secrets,
  AI authentication choice stored in `secrets/`), `doctor.sh`, `backup.sh`, `restore.sh` (path-traversal checks,
  keeps the previous data), `wait-healthy.sh`.
- Tests: 30 unit tests (wizard, compose security invariants, restore safety) and an operational end-to-end test
  (first start, backup, restore, crash recovery). CI jobs for shellcheck, unit tests and the operational test.
- Installation and administration guides.
- Repository bootstrap: license, governance files, architecture decision records 0001–0007, threat model,
  task board, CI for secret scanning, workflow linting and documentation link checks.
