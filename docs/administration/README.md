# Administration

> Phase 1: these tasks use scripts and the server console. The `mcai` SSH console (Phase 4) will replace most of
> them with role-checked commands.

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
the next restart. This is deliberate: operators can run every command. Finer-grained roles arrive with the
platform's RBAC in Phase 4.

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
settings changed. To add mods, append `project:versionId` entries from Modrinth to `MC_MODS`. Pin exact
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

**Who can build:** players with permission level `AI_BUILD_MIN_OP_LEVEL` (default 4). The ops list is reset to
the owner at every start, so by default that's the owner and the server console. To let a trusted builder use it
temporarily, lower the level or give them op. Proper roles arrive in Phase 4.

**Limits** (`.env`, defaults): 40,000 blocks, a 48×64×48 box, within 64 blocks of the player, and $1.00 per
design. TNT, command blocks, lava, fire, bedrock, spawners, portals and similar blocks are always refused.

**Protected areas:** create `data/gateway/protected-areas.json` to keep the AI away from places such as spawn or
someone's base:

```json
[{"name": "spawn", "dimension": "minecraft:overworld", "from": [-50, -64, -50], "to": [50, 319, 50]}]
```

The gateway reads it for every build. If the file has a syntax error, every build is refused until it is fixed.

**Undo snapshots** are stored in `data/minecraft/world/generated/mcai/structures/undo/<id>.nbt`. They are part of
the world, so backups include them.

**Where to look:**
- `docker compose logs ai-worker`: one JSON line per job, with outcome, cost and duration. The question text
  isn't logged.
- `docker compose logs gateway`: the gateway's service log.
- `data/gateway/audit/audit.jsonl`: accepted, rejected and failed requests and failed authentications, with UTC
  timestamps. The request text is stored only as a length and hash unless `AUDIT_LOG_TEXT=true`.
- `data/gateway/gateway.db`: request history (SQLite).

**Troubleshooting:**
- **"The AI is not set up on this server yet":** no credential is stored, or `AI_AUTH_METHOD=none`.
- **"Sorry, the AI could not answer that right now":** the worker log shows why. Typical causes are an invalid
  key, an exhausted plan, or the per-request budget being too low.
- **`/ai` is unknown:** check that `dist/mod/` contains the jar (`scripts/build-mod.sh`), and look for `[mcai]` in
  `docker compose logs minecraft`.
