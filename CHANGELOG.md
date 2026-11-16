# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may break).

## [Unreleased]

### Added
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
