# Third-party components

This repository contains only the project's own code, docs and example content, under Apache-2.0
([LICENSE](../../LICENSE)). It doesn't include or redistribute the software below. Each part is downloaded from
its own source when you build or run the platform, and stays under its own license.

## Downloaded when you install or run

| Component | Used for | License |
| --- | --- | --- |
| Minecraft Java Edition server (Mojang) | The game server, downloaded at first start after you accept the EULA | [Minecraft EULA](https://www.minecraft.net/eula) |
| [itzg/minecraft-server](https://github.com/itzg/docker-minecraft-server) | Base of the `minecraft` image | Apache-2.0 |
| [itzg/mc-backup](https://github.com/itzg/docker-mc-backup) | `backup` service | MIT |
| [Fabric Loader](https://github.com/FabricMC/fabric-loader), [Fabric API](https://github.com/FabricMC/fabric) | Mod loader and API the `mcai` mod runs on | Apache-2.0 |
| Python 3.12 (`python:3.12-slim-bookworm`) | Base of the gateway, egress-proxy and ssh-gateway images | PSF-2.0; Debian packages under their own licenses |
| OpenSSH server (Debian package) | `ssh-gateway` | BSD-style |
| Node.js 22 (`node:22-bookworm-slim`) | Base of the `ai-worker` image | MIT; Debian packages under their own licenses |
| [Claude Code](https://www.npmjs.com/package/@anthropic-ai/claude-code) (`@anthropic-ai/claude-code`) | Talks to Claude inside `ai-worker`; installed from npm when you build that image | Proprietary, © Anthropic PBC; use is subject to Anthropic's terms |
| [Textual](https://github.com/Textualize/textual) | `mcai tui` (optional, installed by `mcai tui --install`) | MIT |

## Used to build the mod

| Component | License |
| --- | --- |
| [Gradle](https://gradle.org/) (`gradle` image) and Eclipse Temurin JDK | Apache-2.0; GPL-2.0 with Classpath Exception |
| [Fabric Loom](https://github.com/FabricMC/fabric-loom) | MIT |
| [Yarn mappings](https://github.com/FabricMC/yarn) | CC0-1.0 |
| JUnit 5 (tests only) | EPL-2.0 |

## Used only in CI and development

pytest (MIT), ruff (MIT), shellcheck (GPL-3.0, run as a tool), actionlint (MIT), gitleaks (MIT), lychee
(Apache-2.0), Trivy (Apache-2.0), Syft (Apache-2.0), mermaid-cli (MIT). None of them is shipped.

## Example content

Atlantis (`examples/atlantis/`, the generator, and the pack files on the release page) is original work of this
project under Apache-2.0. See [ADR 0005](../architecture/adr/0005-original-atlantis.md).

Minecraft is a trademark of Mojang Synergies AB. This project isn't affiliated with Mojang or Microsoft, or
endorsed by them.
