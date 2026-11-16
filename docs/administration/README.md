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

**What the AI can do in this version:** answer questions in text, nothing else. It has no tools: no commands, no
world changes, no files, no web access.

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
