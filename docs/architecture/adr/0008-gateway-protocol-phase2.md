# 0008 — Gateway protocol and AI runtime (Phase 2)

**Status:** Accepted (2026-11-16)

**Context.** The Fabric mod, the gateway and the AI worker need a simple, testable way to talk. It must work
without exposing anything outside the Docker internal network, and keep the AI credential confined to one
container.

**Decision.**
- **Transport:** plain HTTP + JSON on the `internal` Docker network (`http://gateway:8080`), with no TLS inside the
  host. The mod and the worker each use their own randomly generated bearer token (Docker secrets). Each token only
  opens its own API.
- **Direction:** both the mod and the worker *pull* with long-polls (`/v1/mod/events`, `/v1/worker/jobs`), so the
  gateway never needs to connect to them. This avoids open ports in the game server and the worker.
- **Gateway:** Python standard library only, with no third-party packages and therefore no dependency supply
  chain. State lives in SQLite (WAL) and the audit log is JSON lines with UTC timestamps. Requests left unfinished
  at a restart are failed, never re-run, so nothing executes twice.
- **AI worker:** a separate container with Claude Code pinned. It runs `claude -p` per job with `--tools ""`, an
  empty strict MCP config, `--max-turns 1`, `--max-budget-usd` and `--no-session-persistence`. It runs as non-root,
  with a read-only root filesystem, tmpfs home, no host mounts except its two read-only secrets, and outbound
  network access only. A `mock` provider serves CI.
- **Limits** (all configurable): per-player rate, one pending request per player, global queue size, per-request
  and per-day USD caps, request timeout, and maximum prompt and reply lengths.

**Consequences.**
- Phase 2 is questions only (`kind: ask`). Phase 3 adds `build` proposals through MCP tools served by the gateway,
  using the same queue and limits.
- Egress from the AI worker isn't restricted to Anthropic's API by domain yet: it has general outbound access on a
  separate network. An egress allowlist proxy is planned for Phase 7.
