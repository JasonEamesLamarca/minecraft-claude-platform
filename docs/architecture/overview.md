# Architecture overview

> Implemented through Phase 4 (0.4.0). `automation` arrives in Phase 5.

## Components

| Service | Responsibility | Trust level | Talks to |
| --- | --- | --- | --- |
| `minecraft` | itzg/minecraft-server (Fabric) + the `mcai` server mod: `/ai` commands, join/leave + health reports, previews, block placement, undo snapshots. **Implemented** | Untrusted input (players) | gateway (token-authenticated API) |
| `gateway` | Identities (player UUIDs, accounts), roles, request routing, plan validation, approvals, rate and budget limits, management API, audit log, SQLite state. **The only component that decides.** **Implemented** | Trusted core | minecraft (mod API; RCON on the internal network for management commands), ai-worker, ssh-gateway |
| `ai-worker` | Runs Claude Code per job with no tools (questions) or two MCP proposal tools (builds). Holds the AI credential. **Implemented** | Semi-trusted (model output is untrusted) | gateway; internet egress to the AI provider |
| `ssh-gateway` | OpenSSH (unprivileged, keys only), `ForceCommand` → dispatcher; no shell, PTY, forwarding or files. **Implemented** | Edge | gateway management API |
| `backup` | itzg/mc-backup: scheduled world backups. **Implemented** | Data | minecraft (RCON, read-only data) |
| `automation` | Event triggers + scheduler, dedup, loop/rate guards (Phase 5) | Trusted core | gateway |

The management API runs inside the gateway (ADR-0010), not as a separate service. The trust boundaries stay the
same: the SSH gateway only authenticates and forwards, and the gateway authorizes every command.

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
