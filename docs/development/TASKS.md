# Task board

Legend: ✅ done · 🔄 in progress · ⏳ not started · ⚠️ blocked. "Verified" means it actually ran, with the
evidence named. Unverified items say so.

## Phase 0 — Bootstrap
| Task | Status | Evidence |
| --- | --- | --- |
| Repo skeleton, LICENSE (Apache-2.0), NOTICE, governance files | ✅ | this commit |
| ADRs 0001–0007, architecture overview, threat model v0 | ✅ | `docs/architecture/`, `docs/security/` |
| `.gitignore`, `.env.example`, gitleaks config, pre-commit | ✅ | gitleaks run locally (see CHANGELOG) |
| Private-identifier deny-list check (`scripts/check-private.sh`) | ✅ | run locally against working tree + history |
| CI: secret scan, actionlint, link check; Dependabot for Actions | ✅ | first run on GitHub |
| Private GitHub repo created | ✅ | `gh repo view` |

## Phase 1 — Minecraft + compose ✅
| Task | Status | Evidence |
| --- | --- | --- |
| `compose.yaml`: Fabric 1.21.1 + Fabric API via itzg 2026.10.1 (digest-pinned, amd64+arm64), healthcheck, mem/CPU/PID limits, log rotation | ✅ | Healthy on laptop (Pop!_OS 26.04, Docker 29.1.3, Compose 2.40.3), 16 Nov 2026 |
| Hardening: non-root (host UID), read-only rootfs, cap_drop ALL, no-new-privileges, bridge networks, RCON internal-only via secret | ✅ | `docker inspect`; `test_compose_security.py`; port 25575 not published |
| Secure defaults: online-mode, whitelist enforced, secure profile, owner is the only op (reset each start), whitelist merged | ✅ | `whitelist.json`/`ops.json` checked after restart with a test player |
| Setup wizard: prereqs, explicit EULA, owner/port/memory validation, generated secrets (600/700), AI auth choice → `secrets/`, idempotent | ✅ | `test_setup_wizard.py` (14 tests); manual run |
| Backups (itzg/mc-backup 2026.10.3, save-off/flush), on-demand backup, restore with traversal checks + kept previous data | ✅ | Marker-block backup → break → restore → marker back (manual + operational test) |
| Crash recovery (kill java → auto restart, world persists) | ✅ | Operational test, local run 16 Nov 2026 |
| `doctor.sh` diagnostics (no secret output) | ✅ | Run inside operational test |
| Real client join | ⏳ | **Needs maintainer**: join `localhost:25566` with a 1.21.1 client |
| Multi-world, world import/export | ⏳ | Moved to Phase 4 (`mcai` world commands) |
| Docker Desktop macOS/Windows, native ARM64 | ⚠️ | Unverified (no hardware); images are multi-arch |

## Phase 2 — Working core ⏳
Fabric mod `/ai help|ask|status`, gateway API + token auth, ai-worker with mock + Claude providers (verify ADR-0003
first), end-to-end `/ai ask`.

## Phase 3 — Build actions ⏳
Action schema v1, executor (stepped forceload, batching), preview/approve, snapshot/rollback, security tests.

## Phase 4 — Management, RBAC, audit ⏳
Management API, roles, UUID identities, audit log, SSH gateway, `mcai` CLI, TUI port.

## Phase 5 — Automation engine ⏳
## Phase 6 — Atlantis (original; Lite + Full; examples CLI) ⏳
## Phase 7 — Hardening + full CI (tests, buildx multi-arch, Trivy, SBOM, release) ⏳
## Phase 8 — Docs, diagrams, genuine screenshots ⏳
## Phase 9 — Publication-readiness report → owner approval → public ⏳

## Risks / open issues
- ADR-0003 must be re-verified against current Anthropic docs before Phase 2.
- Real-client acceptance steps and screenshots need the maintainer at a Minecraft client.
- macOS / Windows Docker Desktop and native ARM64 hosts can't be verified locally. CI covers ARM64 builds only.
- RCON listens on all container interfaces. It is unpublished and the internal network has no outside route, but
  processes on the Docker host itself could reach it on the container's `edge` IP (password-protected).
  Accepted residual risk; revisit when the gateway lands.
- itzg/mc-backup's scheduled loop and `scripts/backup.sh` could overlap if run at exactly the same time.
