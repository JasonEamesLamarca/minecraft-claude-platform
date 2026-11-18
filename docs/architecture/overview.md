# Architecture overview

> Implemented through Phase 6 (0.6.0).

## Components

| Service | Responsibility | Trust level | Talks to |
| --- | --- | --- | --- |
| `minecraft` | itzg/minecraft-server (Fabric) + the `mcai` server mod: `/ai` commands, join/leave + health reports, previews, block placement, undo snapshots, example packs (verified `.schem` sections placed chunk by chunk), game events for automations (areas, advancements, `/ai trigger`, world time). **Implemented** | Untrusted input (players) | gateway (token-authenticated API) |
| `gateway` | Identities (player UUIDs, accounts), roles, request routing, plan validation, approvals, rate and budget limits, management API, automation engine, example pack store and installer, audit log, SQLite state. **The only component that decides.** **Implemented** | Trusted core | minecraft (mod API; RCON on the internal network for management commands), ai-worker, ssh-gateway |
| `ai-worker` | Runs Claude Code per job with no tools (questions) or two MCP proposal tools (builds). Holds the AI credential. **Implemented** | Semi-trusted (model output is untrusted) | gateway; internet egress to the AI provider |
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

## High-level flow

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
- `ai-egress` connects ai-worker ↔ internet (AI provider) and ai-worker ↔ gateway.
- `edge` carries the published ports: the Minecraft game port and the SSH gateway port (127.0.0.1 unless the
  owner chooses otherwise). Nothing else is published.

## Further reading

- [ADRs](adr/README.md)
- [Threat model](../security/threat-model.md)
- [Task board](../development/TASKS.md)
