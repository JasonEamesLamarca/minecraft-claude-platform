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

Add `--json` before the command for machine-readable output, for example `mcai --json status`.

**Full-screen console:** `pip install textual`, then run `mcai tui`. It has a live log with a command line, the
players (Enter for actions) and the AI requests waiting for approval. It is the successor of the earlier
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
