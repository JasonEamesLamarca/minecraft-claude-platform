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

## Phase 1 — Minecraft + compose ⏳
compose.yaml (internal network, healthchecks, limits), Fabric via itzg pinned by digest, setup wizard (EULA,
secrets, AI auth choice, prereq checks), backup/restore scripts.

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
