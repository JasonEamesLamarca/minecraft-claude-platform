# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may break).

## [Unreleased]

### Added
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
