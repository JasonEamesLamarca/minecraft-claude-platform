# Architecture overview

> Describes version 0.6.2. Every component below is implemented and tested; the
> [task board](../development/TASKS.md) tracks what is still open.

## Components

| Service | Responsibility | Trust level | Talks to |
| --- | --- | --- | --- |
| `minecraft` | itzg/minecraft-server (Fabric) + the `mcai` server mod: `/ai` commands, join/leave + health reports, previews, block placement, undo snapshots, example packs (verified `.schem` sections placed chunk by chunk), game events for automations (areas, advancements, `/ai trigger`, world time). **Implemented** | Untrusted input (players) | gateway (token-authenticated API) |
| `gateway` | Identities (player UUIDs, accounts), roles, request routing, plan validation, approvals, rate and budget limits, management API, automation engine, example pack store and installer, audit log, SQLite state. **The only component that decides.** **Implemented** | Trusted core | minecraft (mod API; RCON on the internal network for management commands), ai-worker, ssh-gateway |
| `ai-worker` | Runs Claude Code per job with no tools (questions) or two MCP proposal tools (builds). Holds the AI credential. **Implemented** | Semi-trusted (model output is untrusted) | gateway; the AI provider only, through `egress-proxy` |
| `egress-proxy` | HTTPS tunnels (CONNECT) to the host names in `EGRESS_ALLOW` only, never to IP addresses or private networks; logs each one. Standard-library Python (`src/mcai/egress.py`). **Implemented** | Edge | ai-worker; the internet |
| `ssh-gateway` | OpenSSH (unprivileged, keys only), `ForceCommand` → dispatcher; no shell, PTY, forwarding or files. **Implemented** | Edge | gateway management API |
| `backup` | itzg/mc-backup: scheduled world backups. **Implemented** | Data | minecraft (RCON, read-only data) |

The management API (ADR-0010) and the automation engine (ADR-0011) run inside the gateway, not as separate
services. The trust boundaries stay the same: the SSH gateway only authenticates and forwards, the gateway
authorizes every command, and automations can only use the allowlisted actions the gateway already has.

## Automations

```mermaid
flowchart LR
  MOD[mod: joins, areas,<br/>advancements, /ai trigger,<br/>heartbeat: world + health] -->|events| ENG
  CRON[scheduler<br/>cron + timezone, intervals] -->|due slots| ENG
  BLD[build reports] -->|build.completed…| ENG
  ENG[engine<br/>conditions · idempotency key ·<br/>loop/rate/AI guards] --> RUN[(runs<br/>SQLite)]
  RUN --> EX[executor<br/>allowlisted actions,<br/>owner's permissions re-checked]
  EX -->|message / broadcast| MOD2[mod]
  EX -->|save, time, weather| RCON[RCON]
  EX -->|ai_message, ai_build| Q[AI queue → worker]
  EX --> AUD[audit log]
```

## Example packs

```mermaid
flowchart LR
  REL[GitHub release<br/>manifest + .schem sections] -->|https, allowlisted,<br/>hash = catalog| CLI[mcai client<br/>admin's computer]
  CLI -->|SSH, examples upload| SSH[ssh-gateway]
  SSH --> GW[gateway<br/>manifest schema, SHA-256,<br/>bounded .schem parse, deny-list]
  GW -->|install: height, protected areas,<br/>overlap, size| EV[event to the mod]
  EV --> MOD[mod: fetch section, SHA-256,<br/>block parser + deny-list]
  MOD --> SNAP[(undo snapshots<br/>per chunk piece)]
  MOD -->|only differing cells,<br/>N blocks per tick| WORLD[(world)]
  SNAP -->|rollback| WORLD
```

The gateway has no internet access: the client downloads, and the gateway re-checks everything it receives
(ADR-0012).

## Asking a question

```mermaid
sequenceDiagram
  actor P as Player
  participant M as minecraft (mod)
  participant G as gateway
  participant W as ai-worker
  participant E as egress-proxy
  participant A as api.anthropic.com
  P->>M: /ai ask how do I make a torch?
  M->>G: request {player UUID, text}
  G->>G: role, rate limit, daily budget
  G-->>M: "Thinking…" (request id)
  W->>G: long poll: next job
  G->>W: job (player text delimited as untrusted)
  W->>E: CONNECT api.anthropic.com:443
  E->>E: allowlist + public address check
  E->>A: TLS tunnel (Claude Code, no tools)
  A-->>W: answer
  W->>G: result {text, cost}
  G->>G: record spend, audit
  M->>G: long poll: events
  G-->>M: answer for the player
  M-->>P: [AI] answer in chat
```

The mod and the worker both *pull* from the gateway (long polls), so the gateway never opens a connection to
anything. If the worker is down, questions wait in the queue; if the gateway restarts, the mod reconnects.

## Building


```mermaid
sequenceDiagram
  actor P as Player
  participant M as minecraft (mod)
  participant G as gateway
  participant W as ai-worker
  P->>M: /ai build "a lighthouse here"
  M->>G: request {player UUID, text, position, world}
  G->>G: RBAC + rate/budget check
  G->>W: job (untrusted text clearly delimited)
  W->>G: MCP propose_build {actions[]}
  G->>G: validate schema, region, block budget
  G-->>M: preview (bounds, block counts, id)
  P->>M: /ai approve <id>
  M->>G: approval
  G->>G: snapshot region, audit
  G->>M: execute allowlisted actions (stepped)
  G-->>P: done / rollback id
```

## Networks and ports

- `internal` (Docker bridge, `internal: true`) connects minecraft ↔ gateway ↔ ai-worker ↔ ssh-gateway ↔ backup.
  RCON exists only here.
- `ai-proxy` (`internal: true`) connects ai-worker ↔ egress-proxy only.
- `ai-egress` connects egress-proxy ↔ internet. No other service is on it.
- `edge` carries the published ports: the Minecraft game port and the SSH gateway port (127.0.0.1 unless the
  owner chooses otherwise). Nothing else is published.

## Further reading

- [ADRs](adr/README.md)
- [Threat model](../security/threat-model.md)
- [Task board](../development/TASKS.md)
