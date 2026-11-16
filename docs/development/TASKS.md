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
| Real client join | ✅ | Maintainer joined `localhost:25566` with a vanilla 1.21.1 client (Prism), 16 Nov 2026, UUID verified in the server log |
| Multi-world, world import/export | ⏳ | Moved to Phase 4 (`mcai` world commands) |
| Docker Desktop macOS/Windows, native ARM64 | ⚠️ | Unverified (no hardware); images are multi-arch |

## Phase 2 — Working core 🔄 (real-Claude test pending)
| Task | Status | Evidence |
| --- | --- | --- |
| ADR-0003 verified against Claude Code docs; terms notice for subscription tokens | ✅ | ADR-0003 "Verification" section |
| Fabric mod: `/ai help|ask|status`, configurable command name + conflict check, permission level, console support, sanitized output | ✅ | Builds with `-Werror`; 7 JUnit tests; loaded on the test server (`[mcai] ready`) |
| Gateway: mod/worker APIs with separate tokens, RBAC stub, rate/pending/queue/budget limits, timeouts, no-duplicate restarts, audit log | ✅ | 21 integration tests (`test_gateway_flow.py`) |
| AI worker: mock + Claude Code (2.1.316, no tools, budget/turn caps), credential only in child env | ✅ | 17 unit tests (`test_ai_core.py`) with a fake `claude` binary |
| Compose: gateway (internal only), ai-worker (internal + egress, no host mounts) | ✅ | `test_compose_security.py`; operational test isolation checks |
| End-to-end `/ai ask` with the mock provider (console → mod → gateway → worker → mod) | ✅ | Operational test, local run 16 Nov 2026 |
| `/ai ask` with real Claude | ⏳ | **Needs the maintainer's credential** (`scripts/setup.sh`) |
| `/ai ask` by a player in game | ⏳ | **Needs maintainer** at the client |
| `/ai status` over RCON shows nothing (async reply after RCON returns) | ⚠️ | Known limitation; works for players |

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
- AI worker egress isn't domain-restricted yet (planned: allowlist proxy, Phase 7).
- Subscription-token use: terms-of-service risk accepted by the owner (ADR-0003).
- Real-client acceptance steps and screenshots need the maintainer at a Minecraft client.
- macOS / Windows Docker Desktop and native ARM64 hosts can't be verified locally. CI covers ARM64 builds only.
- RCON listens on all container interfaces. It is unpublished and the internal network has no outside route, but
  processes on the Docker host itself could reach it on the container's `edge` IP (password-protected).
  Accepted residual risk; revisit when the gateway lands.
- itzg/mc-backup's scheduled loop and `scripts/backup.sh` could overlap if run at exactly the same time.
