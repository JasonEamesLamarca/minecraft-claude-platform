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

## Phase 2 — Working core ✅
| Task | Status | Evidence |
| --- | --- | --- |
| ADR-0003 verified against Claude Code docs; terms notice for subscription tokens | ✅ | ADR-0003 "Verification" section |
| Fabric mod: `/ai help|ask|status`, configurable command name + conflict check, permission level, console support, sanitized output | ✅ | Builds with `-Werror`; 7 JUnit tests; loaded on the test server (`[mcai] ready`) |
| Gateway: mod/worker APIs with separate tokens, RBAC stub, rate/pending/queue/budget limits, timeouts, no-duplicate restarts, audit log | ✅ | 21 integration tests (`test_gateway_flow.py`) |
| AI worker: mock + Claude Code (2.1.316, no tools, budget/turn caps), credential only in child env | ✅ | 17 unit tests (`test_ai_core.py`) with a fake `claude` binary |
| Compose: gateway (internal only), ai-worker (internal + egress, no host mounts) | ✅ | `test_compose_security.py`; operational test isolation checks |
| End-to-end `/ai ask` with the mock provider (console → mod → gateway → worker → mod) | ✅ | Operational test, local run 16 Nov 2026 |
| `/ai ask` with real Claude | ✅ | Claude Code 2.1.316, subscription token: answers in 2-4 s, ~$0.004 estimated per question (16 Nov 2026) |
| Prompt-injection attempts against real Claude (op request, fake closing tag + "use Bash", system-prompt extraction, item/teleport request) | ✅ | All refused in text; `ops.json` unchanged; no tools available to the model |
| `/ai ask` + `/ai status` by a player in game | ✅ | Maintainer, vanilla 1.21.1 client, 17 Nov 2026 |
| `/ai status` over RCON shows nothing (async reply after RCON returns) | ⚠️ | Known limitation; works for players |

## Phase 3 — Build actions ✅
| Task | Status | Evidence |
| --- | --- | --- |
| Action schema v1 + validator (denied blocks, no NBT/namespaces, bounds, size, budget, distance, protected areas, fail-closed) | ✅ | `test_plan.py` (52 tests) |
| MCP tool server (stdio) with `validate_plan` / `propose_build`; Claude runs with no built-in tools | ✅ | MCP tests in `test_plan.py`; real Claude design via console: 9x6x9 oak cabin, 398 blocks, 11 s, $0.029 (17 Nov 2026) |
| Gateway: build requests, re-validation, preview, approve/cancel/undo/history, expiry, rights checks, restart safety | ✅ | `test_build_flow.py` (19 tests) |
| Mod: `/ai build|approve|cancel|undo|history`, block-state check at preview, particle outline, per-tick placement, structure snapshot + undo | ✅ | 12 JUnit tests; console lifecycle on the laptop (build → verify blocks → undo → verify restored) |
| Old mod jars removed on upgrade (`REMOVE_OLD_MODS_INCLUDE=mcai-fabric-*.jar`) | ✅ | 0.2.0 → 0.3.0 upgrade on the laptop |
| Operational test: build lifecycle + forbidden-block rejection | ✅ | Local run 17 Nov 2026 |
| In-game build by the owner (preview outline, approve, undo) | ✅ | Maintainer, in game, 17 Nov 2026 |

## Phase 4 — Management, RBAC, audit ✅ (maintainer checks pending)
| Task | Status | Evidence |
| --- | --- | --- |
| Six roles, permission matrix, assignment rules (below you, below your role, no self, owners only from the host) | ✅ | `test_rbac_keys.py`, `test_admin_escalation_attempts_fail` |
| Player identities by UUID (join/leave tracking, sessions, owner bootstrap from `MC_OWNER` in online mode) | ✅ | `test_management.py` identity tests |
| `/ai build` needs the builder role (replaces `AI_BUILD_MIN_OP_LEVEL`); staff decide on anyone's builds | ✅ | `test_build_flow.py` (21 tests) |
| Management API in the gateway: 37 commands (status, players, whitelist, logs, server, AI, audit, accounts, backups) | ✅ | `test_management.py` (36 tests incl. fake RCON) |
| SSH gateway container (unprivileged OpenSSH, ForceCommand, no PTY/forwarding, key sync, auth events audited) | ✅ | `test_ssh_gateway_is_locked_down`; operational test; manual bypass probes on the laptop |
| `mcai` CLI (Linux/macOS/Windows, pinned host keys, RPC mode for the TUI) | ✅ | `test_cli.py`; used against the laptop stack |
| Wizard: management token, owner SSH key, bind address, host key printed; mod checksum recreates the server | ✅ | `test_setup_wizard.py` |
| Hash-chained daily audit files, retention, export (jsonl/csv), verify | ✅ | audit tests in `test_management.py` |
| Account ↔ player linking (`mcai link`, `/ai link <code>`) | ✅ | `test_link_code` |
| Server health from the mod (TPS, heap, players) | ✅ | `test_sessions_and_heartbeat`; laptop |
| Undo snapshot pruning (after undo; after `AI_UNDO_KEEP_DAYS`) | ✅ | pruning tests; operational test (file deleted after undo) |
| `mcai server restart` (RCON stop + restart policy) | ✅ | Laptop, 17 Nov 2026 |
| Host-only role changes (`scripts/set-role.sh`) | ✅ | `test_host_admin_sets_owner` |
| Join with a real client: owner role assigned, `/ai link`, builder role for a second player | ⏳ | **Needs maintainer** |
| Stack lifecycle (stop/start), restore and upgrades over SSH | ⚠️ | Deliberately host-only (no Docker socket); see ADR-0010 |
| 4b: `mcai tui` (port of `mc-admin`: log + command line, players, AI queue, status bar) over one RPC session | ✅ | `test_tui.py` (headless); smoke test against the laptop stack |
| Interactive Claude Code session from the console | ⚠️ | Not offered: it needs a PTY inside the platform, which ADR-0010 rules out. Revisit with a sandboxed design |

## Phase 5 — Automation engine ✅ (in-game checks pending)
| Task | Status | Evidence |
| --- | --- | --- |
| Definitions (TOML/JSON): id, name, description, owner, trigger, conditions, actions from a fixed list, state, timezone, limits; strict validation (unknown keys, placeholders, ranges) | ✅ | `test_automation_defs.py` (44 tests) |
| Scheduler: 5-field cron in IANA timezones (DST gap skipped, repeated hour once), epoch-aligned intervals, misfire policy | ✅ | `test_cron.py` (32 tests), schedule tests in `test_automations.py` |
| Events: join/first join/leave, areas, advancements (mixin), `/ai trigger`, build results, server start, thresholds, world day/night/rain | ✅ | `test_automations.py`; `AreaWatcherTest` (mod); operational test (world event from the heartbeat) |
| No duplicate runs (idempotency keys), restart without repeats, interrupted runs not repeated, retries resume at the failed action | ✅ | `test_restart_never_repeats_a_slot`, `test_run_interrupted_by_a_restart_is_not_repeated`, `test_retry_resumes_at_the_failed_action` |
| Guards: loops/chains, runs per hour, cooldown, global runs/minute, AI USD/tokens per day, AI calls/hour, failure suspension, timeouts | ✅ | guard tests in `test_automations.py` |
| Owner permissions re-checked each run; privileged automations need the owner; AI proposals need approval | ✅ | `test_owner_losing_a_permission_suspends`, `test_privileged_automation_needs_the_owner`, `test_ai_proposals_need_approval` |
| `mcai automations …` (15 commands), file import over stdin, TUI panel (F7) | ✅ | `test_management_permissions` and other command tests; `test_cli.py`; `test_tui.py`; operational test |
| 11 example automations | ✅ | `automation-examples/`; validated in `test_automation_defs.py` |
| Operational: import over SSH, forbidden action refused, run reaches the game, night event, mixin loads | ✅ | `tests/operational/first_start.sh` (laptop, 17 Nov 2026) |
| In game with a real client: area greeting, `/ai trigger tour`, advancement event, welcome on first join | ✅ | Maintainer, 17 Nov 2026 |
| Real Claude: `mcai automations propose` with the configured credential | ✅ | Maintainer, 17 Nov 2026 (proposal `evening-logoff-reminder`) |
## Phase 6 — Atlantis (original; Lite + Full; examples CLI) ✅

| Task | Status | Verified by |
| --- | --- | --- |
| Generator ported (stdlib Python, 25 test files) to `examples/atlantis/generator`, private profile/deploy/reference tools left out | ✅ | `tests/run_all.py` (25/25) |
| IP clean-up (D2): ring gate, dialling device, chevrons, hover-craft models, power crystals, control chair, franchise names replaced by original designs | ✅ | term search clean, `check-private.sh`, ADR-0005 note |
| Packs: deterministic Sponge v3 `.schem` sections + manifest (SHA-256, offsets, places of interest); Lite (sealed cutaway, 6 sections, 0.2 MB) and Full (90 sections, 4.7 MB) | ✅ | `test_pack.py`; `scripts/build-examples.sh` (identical hashes on rebuild with the same zlib) |
| Catalog with pinned manifest hashes and compression-independent content digests; `build-examples.sh --check` for CI | ✅ | `test_shipped_catalog_is_valid`; same digest with zlib 1.2.13 (Python 3.12 image) and 1.3.1 |
| Gateway: pack store, strict manifest + bounded `.schem` validation, deny-list, installs with site checks, repair, rollback, cancel, forget, interruption handling | ✅ | `test_examples.py` (88), `test_examples_flow.py` (14) |
| `mcai examples …` (14 commands); client-side download with URL allowlist, pinned hashes, `--from` local builds | ✅ | `test_cli.py`, `test_examples_flow.py` |
| Mod 0.6.0: `ExamplePlacer` (hash + NBT limits + block parser + deny-list, snapshots per chunk piece, diff placement, budget per tick, rollback) | ✅ | `ExamplePlacerTest`; operational test |
| Automations: example areas, `example_tour`, `example_restore` (privileged); 3 Atlantis automations | ✅ | `test_example_area_follows_the_install`, `test_example_tour_and_restore` |
| Malicious-manifest / hostile-file tests | ✅ | `test_examples.py` |
| Operational: Lite downloaded via the CLI, installed on a fresh test server, blocks verified over RCON, rolled back | ✅ | `tests/operational/first_start.sh` (laptop, 18 Nov 2026) |
| Renders of Lite (labelled renders) | ✅ | `examples/atlantis/screenshots/` |
| GitHub release `examples-1.0.0` with the pack files | ⏳ | Needs the owner's go-ahead (release assets of the private repo) |
| In game with a real client: walk through Lite, arrival + tour automations, repair, rollback, reinstall; 10 screenshots | ✅ | Maintainer, vanilla 1.21.1 client, 19 Nov 2026; `examples/atlantis/screenshots/game-*.jpg` |
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
