# Test results

What was tested for version **0.6.2**, on what, and with which versions. "Verified" means the test was run and
passed. Anything not run is listed under [Not verified](#not-verified), not claimed.

Recorded on 20 November 2026.

## Summary

| Test | Where | Result |
| --- | --- | --- |
| Unit + integration (pytest), 517 tests | Developer laptop, CI `unit-tests` | **Pass** |
| Mod unit tests (JUnit, 22 tests) and build | Pinned Gradle container, CI `mod-build` | **Pass** |
| Atlantis generator tests (26 files) | CI `atlantis` | **Pass** |
| Lint (ruff), shellcheck, actionlint, gitleaks, links (lychee) | CI | **Pass** |
| Images for linux/amd64 and linux/arm64, Trivy (no fixable HIGH/CRITICAL), SBOMs | CI `images` | **Pass** (arm64: built, not run) |
| Operational: the whole stack, mock AI (`tests/operational/first_start.sh`) | CI `operational`, laptop | **Pass** |
| Clean-machine install from the installation guide (`tests/operational/clean_machine.sh`) | Fresh Ubuntu 24.04 host | **Pass** (27 min, commit `35cf5ad`) |
| Release workflow, dry run | GitHub Actions (`workflow_dispatch`) | **Pass** (attestation step only runs on tags) |
| Release `v0.6.2` from the tag | GitHub Actions | **Pass**: jar, 5 SBOMs, `SHA256SUMS` match; each file has a SLSA v1 provenance attestation naming `release.yml`, `refs/tags/v0.6.2` and commit `e77b77e` |
| Public access without a GitHub login | Repository page, `git clone`, README images, example packs | **Pass**: both packs' manifests and the sections checked match the hashes pinned in `examples/catalog.json` |
| Mermaid diagrams render (11 diagrams, mermaid-cli 11.4.2) | Laptop | **Pass** |
| In game with a real Minecraft client: join, `/ai ask` with real Claude, `/ai build` → approve → undo, Atlantis Lite and Full install, automations | Test server on the laptop | **Pass** (19-20 Nov; [screenshots](#screenshots)) |

CI: run [44831244762](https://github.com/JasonEamesLamarca/minecraft-claude-platform/actions/runs/44831244762)
on commit `e77b77e` (tagged `v0.6.2`): all 10 jobs green. Release:
[44833627174](https://github.com/JasonEamesLamarca/minecraft-claude-platform/actions/runs/44833627174). Release dry run:
[44634548536](https://github.com/JasonEamesLamarca/minecraft-claude-platform/actions/runs/44634548536).

## What the operational test runs

`tests/operational/first_start.sh` builds and starts the real stack from a copy of the repository and checks, in
order:

1. The setup wizard (non-interactive) with the mock AI provider and an owner SSH key; the server becomes healthy.
2. The owner is whitelisted and the only operator, by UUID. RCON isn't published.
3. `/ai ask` round trip: mod → gateway → worker → mod.
4. `/ai build` → preview → `/ai approve` → blocks placed → `/ai undo` → area restored. A plan with a forbidden block
   (TNT) is rejected.
5. SSH administration: the owner over `mcai`; an auditor is read-only; role escalation is refused; a revoked key
   stops working.
6. Automations: a definition uploaded over SSH, a manual run reaches the game, a world event fires from the
   heartbeat.
7. Atlantis Lite through the `mcai` client: checked, installed, rolled back.
8. Isolation: only the game port and the SSH port (localhost) are published; the AI worker has no host mounts.
9. Backup, break a marker block, restore, the marker is back.
10. Crash recovery: the server process is killed, Docker restarts it, the world persists.
11. Failures: a gateway restart (the mod reconnects); the AI worker down (the question waits and is answered);
    the egress proxy refuses a host that isn't allowlisted and the worker has no direct route; the server killed
    during an example install (the install shows as interrupted and `repair` finishes it).
12. `scripts/doctor.sh` is green.

## Clean-machine install

`tests/operational/clean_machine.sh` starts a privileged Ubuntu 24.04 container with nothing on it, installs
Docker from Docker's apt repository (as the [installation guide](../installation/README.md#1-install-docker) links
to), creates a non-root user in the `docker` group, and then follows the guide as that user: `git clone`, the
interactive `scripts/setup.sh` (answered through a terminal: EULA, owner name, default port, 2G heap, no AI
credential, local SSH, default key, start), `scripts/wait-healthy.sh`, `scripts/doctor.sh`, the `mcai remote add`
line the wizard printed, `mcai status` and `mcai players list`.

Result on 20 November 2026: **passed in 27 minutes**, most of it downloads and image builds. The wizard
built the mod and the four platform images, and started all six services.
`minecraft` was healthy after 87 seconds, `doctor.sh` reported no warnings, and `mcai status` answered over SSH
(20.0 TPS, heap 1,220 of 2,048 MB).

Earlier runs found two bugs, both fixed before this run: after an upgrade the wizard kept the old mod jar, and an
owner name without a Minecraft account made the server restart in a loop. The wizard now rebuilds the mod when
its version changes, and checks the name. In this run Mojang's API answered the name check with HTTP 429
(rate-limited), so the wizard warned and went on, as designed. The check now asks Playerdb first, the service the
server itself uses, and was verified by hand against a real and a made-up name.

## Screenshots

Genuine, from the test server on the developer laptop with a Java Edition 1.21.1 client, on 19 and 20 November
2026. Converted from the client's PNGs with the metadata removed. Player names, UUIDs and the SSH key fingerprint
are covered with grey boxes in the terminal shots; nothing else is edited.

| What | File |
| --- | --- |
| `/ai ask` answered by Claude | [game-ai-ask.jpg](../images/game-ai-ask.jpg) |
| `/ai build` preview with the particle outline | [game-ai-build-preview.jpg](../images/game-ai-build-preview.jpg) |
| `/ai approve`: build finished, 254 blocks | [game-ai-build-finished.jpg](../images/game-ai-build-finished.jpg), [game-ai-build-result.jpg](../images/game-ai-build-result.jpg) |
| `/ai status` | [game-ai-status.jpg](../images/game-ai-status.jpg) |
| `mcai tui`, `mcai status`, `mcai audit show` | [terminal-tui.png](../images/terminal-tui.png), [terminal-status.png](../images/terminal-status.png), [terminal-audit.png](../images/terminal-audit.png) |
| Atlantis in game (10 shots) | [examples/atlantis](../../examples/atlantis/README.md#in-game) |

## Versions

| Component | Version |
| --- | --- |
| Platform (mod, gateway, CLI) | 0.6.2 |
| Minecraft / Fabric loader / Fabric API | 1.21.1 / 0.20.1 / 0.116.17+1.21.1 |
| `itzg/minecraft-server` | 2026.10.1-java21 (`sha256:07ab333a…`) |
| `itzg/mc-backup` | 2026.10.3 (`sha256:f86cf846…`) |
| Python images (gateway, egress proxy, SSH gateway) | python:3.12-slim-bookworm (`sha256:935c3c5b…`) |
| AI worker | node:22-bookworm-slim (`sha256:e9f565d3…`), Claude Code 2.1.316 |
| Mod build | gradle:9.9.1-jdk25 (`sha256:39d599d8…`), compiled for Java 21 |
| Developer laptop | Pop!_OS 26.04 LTS, Linux 7.2, x86_64, 16 cores, 14 GB RAM; Docker Engine 29.1.3, Compose 2.40.3 |
| CI runner | GitHub Actions `ubuntu-24.04`, x86_64 |
| Clean-machine host | Ubuntu 24.04 (`sha256:ab18ecc7…`), Docker CE 29.10.0, Compose 5.8.0 from Docker's apt repository |
| Minecraft client (in-game tests) | Java Edition 1.21.1 (Prism Launcher) |

## Not verified

- **macOS and Windows with Docker Desktop.** Expected to work (the images are standard Linux containers and the
  scripts are bash), but not run.
- **Native ARM64 hosts** (Raspberry Pi 5, Apple silicon under Linux). CI builds the arm64 images; nothing has run
  them.
- **The attestation signatures.** The attestations exist and their statements were read and match (above), but
  the signatures weren't checked with `gh attestation verify`: the `gh` on the developer laptop is too old for
  that command. To check them yourself, see [Verifying a release](../security/README.md#verifying-a-release).
- **More than a handful of players at once.** The in-game tests used one or two clients.
