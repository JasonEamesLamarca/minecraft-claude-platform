# Administration

> Day-to-day administration works remotely with the `mcai` command line over SSH, and every command is checked
> against your role: see [Remote administration and roles](#remote-administration-and-roles). The scripts and
> `docker compose` commands below are for the host itself: installing, upgrading, backups and restores.

## Remote administration and roles

### Connect

`scripts/setup.sh` asks for your SSH **public** key (default `~/.ssh/id_ed25519.pub`; create one with
`ssh-keygen -t ed25519`) and registers it for the `owner` account. At the end it prints the command to run on
the computer you administer from. That computer needs Python 3.9+ and the OpenSSH client, which is built into
Windows 10+, macOS and most Linux systems.

```bash
management-cli/mcai remote add home nas.local --port 2222 --host-key 'ssh-ed25519 AAAA... mcai-ssh-gateway'
management-cli/mcai whoami
management-cli/mcai status
```

On Windows use `management-cli\mcai.cmd`. Alternatively, `pip install ./management-cli` installs a `mcai` command.

The host key is **pinned**: if the server's key ever changes, `mcai` refuses to connect instead of trusting the
new one. To see the key on the server, run
`docker compose exec ssh-gateway ssh-keygen -lf /data/ssh_host_ed25519_key.pub`.

By default SSH listens on this computer only (`MCAI_SSH_BIND=127.0.0.1`). Re-run the wizard and answer "yes" to
"Allow administration from other computers" to listen on your network. **Don't** forward port 2222 on your
router: use a VPN to administer from outside.

### Commands

`mcai help` lists everything, and `mcai help <command>` shows the details. The most used:

| Task | Command | Needs |
| --- | --- | --- |
| Server, players, AI and backups at a glance | `mcai status` | anyone |
| Players with role and last seen; one player; sessions | `mcai players list` / `info <name>` / `sessions [name]` | auditor, operator+ |
| Change a player's role | `mcai players role Alex builder` | admin+ |
| Whitelist | `mcai whitelist list` / `add <name>` / `remove <name>` | operator+ (list: auditor too) |
| Kick, broadcast | `mcai players kick <name> [reason]`, `mcai say <text>` | operator+ |
| Server log (IPs masked below admin) | `mcai logs -n 100 [--grep text]`, `mcai logs --follow` | auditor, operator+ |
| Save / restart Minecraft | `mcai server save`, `mcai server restart --yes` | admin+ |
| Raw console command | `mcai server command <command>` | owner |
| AI: history, pending, details | `mcai ai history`, `mcai ai pending`, `mcai ai show <id>` | auditor, operator+ |
| AI: approve, cancel, undo any build | `mcai ai approve|cancel|undo <id>` | operator+ |
| AI: switch off/on, daily budget | `mcai ai disable`, `mcai ai enable`, `mcai ai budget 5.00` | admin+ |
| Audit history, export, integrity | `mcai audit show --since 7d [--event role.] [--format csv]`, `mcai audit verify` | auditor, admin+ |
| Accounts and SSH keys | `mcai accounts list|add|role|disable|enable|keys|key-add|key-revoke` | admin+ (own keys: anyone) |
| Backups | `mcai backups list` | auditor, operator+ |
| Automations: list, details, run history | `mcai automations list`, `show <id>`, `runs [id]` (alias `mcai auto`) | auditor, operator+ |
| Automations: import, enable, run, ask the AI | `mcai automations import <file>`, `enable <id>`, `run <id>`, `propose <text>` | admin+ (privileged ones: owner approves) |
| Example builds (Atlantis): catalog, packs, installs | `mcai examples list`, `info <pack>`, `installs`, `status <install>` | auditor, operator+ |
| Example builds: download, install, repair, roll back | `mcai examples download <pack>`, `install <pack> --at X Y Z`, `repair`/`rollback <install>` | admin+ |

Add `--json` before the command for machine-readable output, for example `mcai --json status`.

**Full-screen console:** run `mcai tui --install` once, then `mcai tui`. It has a live log with a command line, the
players (Enter for actions), the AI requests waiting for approval and the automations (F7: enable, disable,
approve, run, history). It is the successor of the earlier
`mc-admin` console. Unlike that console, it never gets a shell on the host: every action is an `mcai` command
checked against your role. Interactive Claude Code sessions on the server are not offered over SSH, because
that would need a terminal (PTY) inside the platform.

### Roles

| Role | In game | Management (SSH) |
| --- | --- | --- |
| **owner** | everything | everything, including raw console commands |
| **admin** | build, decide on anyone's builds | roles and accounts below admin, restart, AI settings, audit |
| **operator** | build, decide on anyone's builds | whitelist, kick, say, logs, AI history and decisions |
| **builder** | `/ai ask`, `/ai build` (own builds) | status |
| **player** | `/ai ask` | status |
| **auditor** | nothing | read-only: status, players, logs, AI history, audit, accounts |

- **Players are identified by UUID.** A player gets the `player` role at their first join. Change it with
  `mcai players role <name> <role>`. Renaming a Minecraft account keeps the role.
- **The owner** is the Minecraft name in `MC_OWNER`: it becomes `owner` at its first join (online mode only).
- **Rules for changing roles:** you can change only people below you, and only to roles below yours. Nobody
  changes their own role. Making someone owner (or demoting an owner) is done on the host:
  `scripts/set-role.sh player <name> owner` or `scripts/set-role.sh account <name> owner`.
- **Accounts** are for people who administer. Give each person their own account and key:
  `mcai accounts add alice operator ssh-ed25519 AAAA... alice@laptop`. The key works within 5 seconds. Revoke a lost key with
  `mcai accounts key-revoke SHA256:...`; it stops working within seconds. Revoked keys can't be added again.
- **Linking** (`mcai link`, then `/ai link <code>` in game) records which player an account belongs to. It grants
  nothing.
- **Vanilla ops** are separate. The ops list is still reset to just the owner at every start, and the platform's
  roles never hand out `/op`.

### Recovery: lost owner key

On the server host, run `scripts/setup.sh --ssh-key /path/to/new_key.pub`, or copy the key to
`data/gateway/bootstrap/owner.pub` and run `docker compose restart gateway`. The gateway adds the key to the
`owner` account and re-enables it. Access to the host is the root of trust.

### Audit

Everything administrative ends up in `data/gateway/audit/audit-YYYY-MM-DD.jsonl` (UTC, one JSON object per line,
mode 600):

- SSH logins (accepted and rejected, with key fingerprint and source IP);
- management commands that change something, and every refused one;
- role, account and key changes;
- player joins and leaves;
- AI requests, approvals, builds and undos.

Each record contains a hash of the previous one, so `mcai audit verify` notices edits and deletions. Files older
than `AUDIT_RETENTION_DAYS` (default 365) are deleted. Back up `data/gateway/` if you need to keep them longer.

## Server console

Send one command (no leading slash) and print the reply:

```bash
docker compose exec minecraft rcon-cli "list"
```

For an interactive console, run `docker compose exec minecraft rcon-cli`. RCON is internal only.

## Players

| Task | Command |
| --- | --- |
| Add to whitelist | `docker compose exec minecraft rcon-cli "whitelist add Name"` |
| Remove from whitelist | `docker compose exec minecraft rcon-cli "whitelist remove Name"` |
| Who is online | `docker compose exec minecraft rcon-cli "list"` |

The server stores players by **UUID** in `data/minecraft/whitelist.json` and `ops.json`. At every start the owner
from `.env` is added to the whitelist again, and players you added are kept.

**Operators:** the operator list is **reset to just the owner at every start**. An `op` given in-game lasts only until
the next restart. This is deliberate: operators can run every command. Use the platform's roles instead
(`mcai players role`).

## Backups

The `backup` service ([itzg/mc-backup](https://github.com/itzg/docker-mc-backup)) does the following:

- It takes a backup `BACKUP_INITIAL_DELAY` after start, then every `BACKUP_INTERVAL` (default 24 h). Backups are
  skipped while nobody is online (`BACKUP_PAUSE_IF_NO_PLAYERS=true`).
- Around each copy it pauses saving and flushes the world (`save-off`, `save-all flush`, then `save-on`), so
  backups are consistent.
- It writes `backups/<level>-YYYYMMDD-HHMMSS.tar.gz` and deletes backups older than `BACKUP_PRUNE_DAYS` (default 14).
- Mod `.jar` files, caches and logs are left out. Mods are downloaded again at start.

Take a backup now:

```bash
scripts/backup.sh
```

Backups sit on the same disk as the server. Copy important ones somewhere else (another disk, NAS or cloud).
Treat them as **sensitive**: they contain `server.properties`, which includes the RCON password, and player UUIDs.
The `backups/` folder is created with mode 700. The RCON client files are excluded from backups.

## Restore

```bash
scripts/restore.sh latest          # or: scripts/restore.sh backups/world-20261116-205604.tar.gz
```

The script does the following:

1. Checks the archive is readable and contains no absolute or `..` paths.
2. Asks you to type `restore`. Use `--yes` to skip the question in scripts.
3. Stops the server and the backup service.
4. **Moves** the current data to `data/minecraft.before-restore-<UTC time>`, so nothing is deleted.
5. Extracts the backup and starts the server again, then waits until it is healthy.

Delete the `before-restore` folder yourself once you are happy with the result.

## Logs

```bash
docker compose logs -f minecraft          # live
docker compose logs --since 1h backup
```

Container logs rotate automatically (5 × 10 MB per service). The server's own logs are in `data/minecraft/logs/`.

## Health and diagnostics

`scripts/doctor.sh` checks prerequisites, configuration (it never prints secret values), service health,
CPU and memory use, and recent warnings. If the server keeps restarting, `docker compose logs --tail 100 minecraft`
usually names the cause: an unaccepted EULA, a mod that doesn't match the Minecraft version, or too little memory.

## Changing settings

Edit `.env`, or re-run `scripts/setup.sh`, then run `docker compose up -d`, which recreates only the containers whose
settings changed. After rebuilding the mod (`scripts/build-mod.sh`), re-run `scripts/setup.sh` so the new jar's
checksum is recorded and `up` restarts the server with it. To add mods, append `project:versionId` entries from Modrinth to `MC_MODS`. Pin exact
versions so updates stay deliberate.

### Memory

The server's Java heap is `MC_MEMORY` in `.env`, and the container's hard limit is `MC_MEM_LIMIT`. Keep the limit
at about 1.5 times the heap plus 1 GB: Java needs room outside the heap, and a container that hits its limit is
killed, not slowed down. **4G is the default and the recommended minimum**. Use 6G before installing Atlantis Full or
for 5-10 players (the [installation guide](../installation/README.md#supported-hosts) has the full table).

To change it, for example to 6 GB:

1. Check the host has the RAM: the new heap plus about 2 GB for the other containers and the system
   (`free -h` on Linux).
2. Edit `.env`:
   ```bash
   MC_MEMORY=6G
   MC_MEM_LIMIT=10g
   ```
   Or re-run `scripts/setup.sh` and enter `6G` when it asks for the heap. It sets the limit for you.
3. Apply it. This restarts the Minecraft server, so tell players first:
   ```bash
   docker compose up -d minecraft
   ```
4. Check it: `mcai status` shows the heap in use and its maximum. `docker stats mcai-minecraft-1` shows the
   container's use against the limit.

Signs the heap is too small: the log repeats `Can't keep up! Is the server overloaded?`, `mcai status` shows the
heap above 90% most of the time, or the server restarts with `java.lang.OutOfMemoryError` in
`docker compose logs minecraft`.

## AI assistant (`/ai`)

Players type `/ai ask <question>`. The answer appears in their chat a few seconds later. `/ai status` shows their
pending requests, today's AI spend and whether the AI worker is online. `/ai help` lists the commands.

| Setting (`.env`) | Default | Meaning |
| --- | --- | --- |
| `AI_PROVIDER` | `claude` | `mock` gives canned answers, which is useful for testing without a credential or cost |
| `AI_MAX_REQUEST_USD` | `0.25` | Cap per request, passed to Claude Code as `--max-budget-usd`. This is an estimate and can be slightly exceeded |
| `AI_DAILY_BUDGET_USD` | `2.00` | Requests are refused once today's spend (UTC) reaches this. `0` turns the AI off |
| `AI_PLAYER_REQUESTS_PER_MINUTE` | `3` | Per-player rate limit |
| `AI_MAX_PENDING_PER_PLAYER` | `1` | A player waits for one answer before asking again |
| `AI_MODEL` | (Claude Code default) | Model alias such as `sonnet` or `haiku`, or a full model name |
| `EGRESS_ALLOW` | `api.anthropic.com` | The only host names the AI worker can reach (HTTPS, through `egress-proxy`). `docker compose logs egress-proxy` shows every connection and refusal |
| `MCAI_COMMAND` | `ai` | Rename the command if another mod already uses `/ai` |
| `MCAI_ASK_PERMISSION_LEVEL` | `0` | Raise it to `2`, for example, to limit `/ai` to operators |

Apply changes with `docker compose up -d`.

**Credential:** run `scripts/setup.sh` again and choose the API key or subscription option. The credential is
stored in `secrets/ai_credential` (mode 600) and only the `ai-worker` container can read it. Then run
`docker compose up -d ai-worker`. Rotating it works the same way.

**What the AI can do in this version:** answer questions, and *design* builds that only happen after someone
approves them (below). It has no other tools: no commands, no files, no web access.

### Building with the AI

```
/ai build a small stone watchtower with a ladder, 3 blocks in front of me
```

1. Claude designs the build. This usually takes 10-60 s, and you can keep playing.
2. You get a **preview**: title, size, coordinates, block count and materials. White particles outline the
   area for 30 s; only you can see them.
3. `/ai approve` (or `/ai approve <id>`) builds it, a batch of blocks per tick. `/ai cancel` drops the proposal.
   Proposals expire after 15 minutes.
4. `/ai undo` (or `/ai undo <id>`) puts the area back exactly as it was before the build.

`/ai history` lists your last 5 requests with their ids and states.

**Who can build:** players with the `builder` role or higher: `mcai players role Alex builder`. Builders approve,
cancel and undo their own builds. Operators and above can do that for anyone's builds, in game or with
`mcai ai approve|cancel|undo <id>`.

**Limits** (`.env`, defaults): 40,000 blocks, a 48×64×48 box, within 64 blocks of the player, and $1.00 per
design. TNT, command blocks, lava, fire, bedrock, spawners, portals and similar blocks are always refused.

**Protected areas:** create `data/gateway/protected-areas.json` to keep the AI away from places such as spawn or
someone's base:

```json
[{"name": "spawn", "dimension": "minecraft:overworld", "from": [-50, -64, -50], "to": [50, 319, 50]}]
```

The gateway reads it for every build. If the file has a syntax error, every build is refused until it is fixed.

**Undo snapshots** are stored in `data/minecraft/world/generated/mcai/structures/undo/<id>.nbt`. They are part of
the world, so backups include them. A snapshot is deleted when its build is undone, and after
`AI_UNDO_KEEP_DAYS` (default 7). After that the build can no longer be undone.

**Where to look:**
- `docker compose logs ai-worker`: one JSON line per job, with outcome, cost and duration. The question text
  isn't logged.
- `docker compose logs gateway`: the gateway's service log.
- `mcai audit show --event request.` (or `data/gateway/audit/audit-*.jsonl`): accepted, rejected and failed
  requests and failed authentications, with UTC timestamps. The request text is stored only as a length and hash
  unless `AUDIT_LOG_TEXT=true`.
- `data/gateway/gateway.db`: request history (SQLite).

**Troubleshooting:**
- **"The AI is not set up on this server yet":** no credential is stored, or `AI_AUTH_METHOD=none`.
- **"Sorry, the AI could not answer that right now":** the worker log shows why. Typical causes are an invalid
  key, an exhausted plan, or the per-request budget being too low.
- **`/ai` is unknown:** check that `dist/mod/` contains the jar (`scripts/build-mod.sh`), and look for `[mcai]` in
  `docker compose logs minecraft`.

## Automations

Automations do something when an event happens in the game or at a set time: greet new players, announce a
daily challenge, warn staff about lag, save the world while people play. They are text files (TOML or JSON), not
code: each one has a trigger, optional conditions, up to 10 actions from a fixed list, and limits. The design and
its guarantees are in [ADR-0011](../architecture/adr/0011-automation-engine.md).

### Getting started

```bash
mcai automations examples                 # 11 examples shipped with the platform
mcai automations install welcome          # installed disabled
mcai automations show welcome             # trigger, actions, limits, permissions it needs
mcai automations enable welcome
mcai automations runs welcome             # history: status, attempts, errors, output of each action
```

To write your own, copy an example from [`automation-examples/`](../../automation-examples/), change it, and
send it from your computer (the `mcai` client reads the file locally):

```bash
mcai automations validate my-event.toml   # check it and see when it would run
mcai automations import my-event.toml     # add --replace to update an existing one
mcai automations run my-event [--player Alex]   # try it now (works while disabled too)
```

Or ask the AI to write one. It only drafts it: nothing runs until you have read it and approved it.

```bash
mcai automations propose "every Saturday at 20:00 Berlin time, announce the weekly build contest"
mcai ai show <request id>                 # a few seconds later: the proposal's id, or why it was invalid
mcai automations show <id>
mcai automations approve <id>             # or: reject <id> [reason]
```

### A definition

```toml
id = "daily-announcement"          # lowercase, digits and dashes
name = "Evening announcement"
description = "Reminds players at 18:00 that the world is backed up."
timezone = "Europe/Berlin"         # default: AUTOMATION_TIMEZONE, else TZ, else UTC
enabled = false                    # state on import (examples are always disabled)

[trigger]
schedule = "0 18 * * *"            # or: every = "30m"   or: event = "player.join" (see below)

[conditions]                       # all optional; all must hold
players_online_min = 1
time_between = ["08:00", "23:00"]  # in the timezone above; may wrap past midnight
days = ["fri", "sat", "sun"]

[[actions]]
type = "broadcast"
text = "It's {time}: {players_online} player(s) online."

[limits]                           # all optional
timeout_s = 60
retries = 0                        # retries resume at the failed action
retry_delay_s = 30                 # doubled each retry
on_error = "stop"                  # stop | continue (with the next action) | disable
max_runs_per_hour = 12
cooldown_s = 0                     # per player for player events, otherwise per automation
misfire = "skip"                   # a slot missed while the server was down: skip, or run once
misfire_grace_s = 300              # up to this late, a missed slot still runs
ai_max_usd_per_day = 0             # must be above 0 to use AI actions
ai_max_tokens_per_day = 50000
```

**Events.**

| Event | Extra trigger keys | Placeholders |
| --- | --- | --- |
| `player.join`, `player.first_join`, `player.leave` | | `{player}` |
| `player.enter_area`, `player.leave_area` | `area = {name, dimension, from = [x,y,z], to = [x,y,z]}` (max 512 per side), or `area = {name, example = "atlantis-lite"}`: the area of that example's latest install | `{player}`, `{area}` |
| `player.advancement` | `advancement = "minecraft:story/*"` (glob on the id; default all) | `{player}`, `{advancement}` |
| `player.trigger` | `name = "tour"`: players type `/ai trigger tour` | `{player}`, `{trigger}` |
| `build.completed`, `build.failed`, `build.undone` | | `{player}`, `{title}`, `{request}`, `{blocks}` |
| `server.started` | | |
| `server.threshold` | `metric` (mspt, tps, players_online, heap_pct), `above` or `below`, `for_s` | `{metric}`, `{value}` |
| `world.day_start`, `world.night_start`, `world.rain_start`, `world.rain_stop` | | |

Every text can also use `{players_online}`, `{time}`, `{date}` and `{automation}`. A misspelled placeholder is
rejected when you import the file. A threshold fires once when the condition has held for `for_s` seconds, and
again only after it has cleared. World time and weather arrive with the mod's heartbeat (every 30 s), and area
changes reach the mod within 30 s of enabling an automation.

**Actions.** These are the only things an automation can do: there is no way to run console commands or code.

| Action | What it does | Permission the owner needs |
| --- | --- | --- |
| `message` (`text`) | chat message to the player who triggered it | players.moderate |
| `broadcast` (`text`) | chat message to everyone online | players.moderate |
| `notify_role` (`text`, `min_role`, default operator) | message to online players with at least that role | players.moderate |
| `log` (`text`) | a record in the audit log (`mcai audit show --event automation.log`) | status.read |
| `status_report` (`to` = log, staff or broadcast) | players online, TPS, AI spend | status.read (+ players.moderate) |
| `save` | flush the world to disk | server.control |
| `world` (`set` = time or weather, `value`) | `time` day/noon/night/midnight, `weather` clear/rain/thunder | server.control |
| `set_role` (`role` = player or builder) | give the triggering player that role; never lowers a role | roles.assign, **privileged** |
| `ai_message` (`prompt`, `to` = broadcast, player or staff, `max_usd`) | asks the AI (text only) and sends the answer | ai.configure |
| `ai_build` (`prompt`, `dimension`, `at = [x,y,z]`) | asks the AI for a build proposal; staff approve it as usual | ai.build, **privileged** |
| `example_tour` (`example`) | tells the triggering player the example's places of interest with their coordinates | players.moderate |
| `example_restore` (`example`) | repairs the example's latest install (see [Example builds](#example-builds-atlantis)) | examples.install, **privileged** |

### Who may do what

- **Read** (list, show, runs, export, examples): auditor, operator, admin, owner.
- **Change** (import, install, enable, disable, run, delete, propose): admin and owner. You must hold every
  permission the automation's actions need, and you become its **owner**. If the owner later loses one of those
  permissions or the account is disabled, the next run is denied and the automation is **suspended**.
- **Privileged** automations (`set_role`, `ai_build`, `example_restore`) created by an admin wait as `pending_approval` until the
  owner runs `mcai automations approve <id>`. AI proposals always wait for approval; privileged ones need the owner.

### Safeguards

- **No duplicates.** Each schedule slot and each event runs at most once, also across restarts. A run that was
  executing when the gateway stopped is marked `interrupted` and not repeated.
- **No loops.** An event caused by an automation (for example a build it requested) can't start that automation
  again, and chains stop after 3 automations.
- **Rates.** `max_runs_per_hour` and `cooldown_s` per automation; `AUTOMATION_MAX_RUNS_PER_MINUTE` (30) overall.
- **AI spend.** Per automation and UTC day: `ai_max_usd_per_day` and `ai_max_tokens_per_day`. Overall:
  `AUTOMATION_AI_CALLS_PER_HOUR` (10), plus the shared daily budget and `mcai ai disable`.
- **Failures.** After 5 failed runs in a row (or 1 with `on_error = "disable"`) the automation is suspended; fix
  the cause and `mcai automations enable <id>`.
- **Audit.** Every change, approval, run result, refused run (`automation.guard`) and skipped slot
  (`automation.misfire`) is in the audit log.

Backups are not an automation action: the backup service makes them on its own schedule (`BACKUP_INTERVAL`),
because starting one needs host privileges that nothing in the platform has. The `autosave` example flushes the
world between backups.

## Example builds (Atlantis)

The platform can install ready-made builds into your world. The first is **Atlantis**, an original underwater
city under a glass dome, made by the project's own generator
([examples/atlantis](../../examples/atlantis/README.md)). It comes in two sizes:

| Pack | What | Size | Install time |
| --- | --- | --- | --- |
| `atlantis-full` | the whole city: ten districts, six piers, a 1,200-block dome and the sea floor | 1,600 x 127 x 1,600, 255 million blocks, 4.7 MB | 30-80 minutes |
| `atlantis-lite` | a 96-block-wide cutaway to try it quickly: the central tower, the Fountain Plaza district, a pier, the dome wall and the sea | 96 x 127 x 768, 9.4 million blocks, 0.2 MB | about a minute |

### Install

```bash
mcai examples list                                   # what the server knows and has
mcai examples download atlantis-full                 # on your computer: download, check, send to the server
mcai examples info atlantis-full                     # size, anchor, places of interest
mcai examples install atlantis-full --at 2000 -56 2000          # preview: area, blocks, time
mcai examples install atlantis-full --at 2000 -56 2000 --yes    # place it
mcai examples status <install>                       # progress; when done: places of interest with coordinates
```

- **Where.** `--at X Y Z` is where the pack's *anchor* goes: the city centre at floor level. People walk 5 blocks
  higher. With Y = -56 (the recommended value) Atlantis fills y -64 to 62, so its glass roof sits at sea level
  in an ocean. Lite is sealed with glass on all sides, so it also works on land. Full needs a deep ocean of about
  1,600 x 1,600 blocks; make a backup before installing it, and give the server a 6 GB heap (see [Memory](#memory)).
- **Checks before anything changes.** The pack must fit in the world's height, may not overlap a protected area
  (`protected-areas.json`, as for AI builds) or another install, and may not exceed `EXAMPLES_MAX_INSTALL_BLOCKS`.
- **What happens.** The server works through the pack chunk by chunk. Before it changes a chunk, it saves what
  is there (an undo snapshot in `world/generated/mcai/structures/examples/`). It then sets only the blocks that
  differ, `EXAMPLES_BLOCKS_PER_TICK` (4000) per tick. Lower that value if the server lags while placing.
- **Keep players away while it installs.** Chunks near a player keep running: water from the old terrain in parts
  not placed yet can flow into parts that are finished, and stays there as frozen streams once those parts are
  placed. If anyone was in or near the area during the install, run `mcai examples repair <install>` once it is
  done: it replaces every block that differs from the pack, including that water.
- **One job at a time.** A second install, repair or rollback waits until the first is done; `mcai examples
  cancel <install>` stops one (it stays partly placed: repair or roll it back afterwards).

### Repair, roll back, forget

```bash
mcai examples repair <install>          # places it again: only blocks that were broken or changed
mcai examples rollback <install> --yes  # restores the area as it was before (including anything built there since)
mcai examples forget <install> --yes    # deletes the undo snapshots to free disk space: no rollback afterwards
```

If the server restarts while placing, the install is marked `interrupted`. Run `repair` to finish it, or
`rollback` to undo it. Both keep working after a restart because the snapshots are files in the world.

### Automations for Atlantis

`mcai automations examples` also lists three Atlantis automations (install them after the pack):

- `atlantis-arrival` greets players who walk into the installed city. Its area follows the install, so there are no
  coordinates to edit.
- `atlantis-tour`: players type `/ai trigger atlantis` and get the sights with coordinates.
- `atlantis-restore` repairs the city every Monday at 05:00. It changes the world, so the owner approves it.

### Trust and safety

- **Where packs come from.** `mcai examples download` runs on your computer, because the server has no internet
  access. It fetches only from this project's GitHub release downloads, over https. It checks the manifest against
  the SHA-256 in the platform's catalog (`examples/catalog.json`) and every section against the manifest, then
  sends the files over SSH (`examples.manage`, admin+).
- **What the server accepts.** The gateway checks every file again. It only accepts block data: no commands,
  scripts, entities or block-entity data (chest contents, signs, command blocks). Blocks such as command blocks,
  structure blocks, spawners, TNT, lava and bedrock are refused, and size, nesting and decompression are capped.
  The mod checks each block with the game's own parser (NBT disabled) before placing.
- **Your own builds.** `mcai examples download <pack> --from <folder>` sends a pack you built yourself
  (`scripts/build-examples.sh`). It goes through the same checks and is labelled *unofficial*.
- Every upload, refusal, install, repair, rollback and snapshot deletion is in the audit log (`examples.*`).

