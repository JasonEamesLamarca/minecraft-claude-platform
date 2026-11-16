# Installation

> Phase 2 status: this installs the **Minecraft server with backups and the `/ai ask` assistant**. Automations,
> the SSH console and AI-assisted building arrive in later phases. Everything below was run on the "verified" setup in the table, unless marked otherwise.

## Supported hosts

| Host | Architecture | Status |
| --- | --- | --- |
| Pop!_OS 26.04 (Ubuntu base), Docker Engine 29.1, Compose 2.40 | x86_64 | **Verified** (manual + `tests/operational/first_start.sh`) |
| GitHub Actions `ubuntu-24.04` runner | x86_64 | **Verified in CI** (operational job) |
| Other Linux with Docker Engine ≥ 24 and Compose ≥ 2.24 (Debian, Ubuntu, Fedora…) | x86_64 / arm64 | Expected to work, **unverified** |
| macOS with Docker Desktop | Apple silicon / Intel | Expected to work, **unverified** |
| Windows 11 with Docker Desktop (WSL 2 backend). Run the scripts inside a WSL 2 distribution | x86_64 / arm64 | Expected to work, **unverified** |

Both images used (`itzg/minecraft-server`, `itzg/mc-backup`) are published for amd64 and arm64.

**Resources:** 2 CPU cores and 4 GB of free RAM are the minimum for a small server (`--memory 2G`). 4 GB heap or
more is recommended for several players. You'll also want 5 GB of free disk space, plus space for backups.

## 1. Install Docker

- **Linux:** follow <https://docs.docker.com/engine/install/> for your distribution, including the *Compose plugin*.
  Then let your user run Docker: `sudo usermod -aG docker $USER`, and log out and back in.
- **macOS / Windows:** install Docker Desktop: <https://docs.docker.com/desktop/>. On Windows, enable the WSL 2
  backend and use a WSL terminal for the commands below.

Check that it works: `docker run --rm hello-world` and `docker compose version` (2.24 or newer).

## 2. Get the platform

```bash
git clone https://github.com/JasonEamesLamarca/minecraft-claude-platform.git
cd minecraft-claude-platform
```

## 3. Run the setup wizard

```bash
scripts/setup.sh
```

It will:

1. Check prerequisites: Docker, Compose version, architecture, disk space.
2. Ask you to read and accept the [Minecraft EULA](https://www.minecraft.net/eula). Nothing is downloaded or
   started unless you type `yes`.
3. Ask for **your Minecraft Java username**. You become the only operator and the first whitelisted player.
4. Ask for the game port (default 25565) and the Java heap size (e.g. `4G`). The container memory limit is set to
   about 1.5× the heap.
5. Generate random internal secrets in `secrets/` (mode 600), such as the RCON password.
6. Ask how Claude should authenticate:
   - an **Anthropic API key** from <https://console.anthropic.com> (pay per use; recommended),
   - a **subscription token** from `claude setup-token`. Read the terms notice the wizard shows, and
     [ADR-0003](../architecture/adr/0003-ai-authentication.md),
   - or **skip**: `/ai` then replies that the AI isn't set up.

   The credential goes to `secrets/ai_credential`, never `.env`, and only the AI worker container can read it.
7. Build the server mod in a container on first run (no Java needed; a few minutes). Then validate
   `compose.yaml` and offer to start everything.

`.env` and `secrets/` are git-ignored. Re-running the wizard keeps your existing secrets and settings.

Non-interactive use (scripts, CI):

```bash
scripts/setup.sh --non-interactive --accept-eula --owner YourName --port 25565 --memory 4G --ai-auth none --start
```

## 4. Start and check

```bash
docker compose up -d          # if you didn't start from the wizard
scripts/wait-healthy.sh minecraft 900
scripts/doctor.sh             # prerequisites, config, health, resource use, recent errors
```

The first start downloads Minecraft, the Fabric loader and Fabric API, and builds the gateway and AI worker
images. This takes a few minutes. Four services run: `minecraft`, `backup`, `gateway` and `ai-worker`.

**Test the AI:** in game, type `/ai ask how do I make a torch?`. Without a credential, set `AI_PROVIDER=mock` in
`.env` and run `docker compose up -d` to get canned answers.

## 5. Connect

In Minecraft Java Edition (same version as `MC_VERSION`, default 1.21.1), choose **Multiplayer → Add Server** and
enter:

- `localhost:PORT` from the same computer,
- `<LAN IP of the host>:PORT` from your home network.

Only whitelisted players can join. To add a friend, see [administration](../administration/README.md).

### Firewall and internet access

- Docker publishes the game port on all interfaces by default (`MC_BIND_ADDRESS=0.0.0.0`). On Linux, **Docker's
  port publishing bypasses `ufw`/`firewalld` rules**. To keep the server LAN-only, rely on your router (don't
  forward the port), or set `MC_BIND_ADDRESS` to a specific LAN IP.
- To let friends join from the internet, forward the TCP game port on your router. Keep the whitelist on.
- RCON (25575) is **never** published. It exists only on the internal Docker network.

## Updating

```bash
git pull
scripts/setup.sh              # adds any new settings, keeps yours
docker compose pull && docker compose up -d
```

Image versions are pinned in `compose.yaml`, so an update changes them only when you pull a new platform version.
Take a backup first (`scripts/backup.sh`).

## Removing the platform

```bash
docker compose down           # stops and removes containers and networks; your data stays
```

Your world is in `data/minecraft/` and your backups are in `backups/`. Delete those folders yourself only once you
are sure you no longer need them.
