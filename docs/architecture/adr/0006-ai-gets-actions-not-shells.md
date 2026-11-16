# 0006 — The AI proposes structured actions and never gets a shell

**Status:** Accepted (2026-11-16)

**Context.** Player chat, signs, books, names and imported files are attacker-controlled text that reaches the
model. A model with a shell, RCON, the console or the world files can be talked into anything those allow: operator
grants, file exfiltration, config changes, deleting backups.

**Decision.**
- The AI worker runs Claude Code **without Bash, file-write or network tools**. Its only tools are MCP tools served
  by the gateway: read-only context queries and *proposal* tools that return JSON actions matching a versioned schema.
- The **gateway** validates each proposal (schema, role permissions of the requesting player's UUID, region bounds,
  block and entity budgets, rate limits). It executes allowlisted actions only, through a single executor that
  owns the server connection (no `op`, `deop`, `whitelist`, `stop`, `execute … run` passthrough, and no
  arbitrary commands).
- World changes above a small threshold need **preview + explicit approval** (`/ai approve <id>`) by a player whose
  role allows it, take a snapshot first, and can be rolled back.
- Permission bypass modes are never the default. The AI container has no Docker socket, no world mount, and network
  egress limited to the AI provider.

**Consequences.** The AI is less "magic" than a free-roaming agent with a shell, so large builds are expressed as
plans the executor runs. Each new capability means a new action type plus tests, which is the intended cost.
