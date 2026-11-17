# ADR-0010: Management over SSH, roles and audit

**Status:** Accepted (Phase 4, 17 Nov 2026)

## Context

The owner needs to administer the server from another computer (Linux, macOS, Windows) without a host shell.
Friends need different rights: some may build with the AI, some may moderate, and someone may only need to read
the history. The brief asks for public-key SSH, no unrestricted shell, server-side authorization, six roles,
UUID identities, linkable accounts and a structured audit trail.

## Decision

**One authority.** The gateway owns identities, roles and the audit log. Every decision is made there: for
players (requests from the mod), for management accounts (commands from the SSH gateway) and for the AI worker.
Clients only display results.

**Roles.** `owner > admin > operator > builder > player > auditor`, one vocabulary with separate assignments:

- **Players** are identified by Minecraft UUID. The mod reports UUID and name; the role comes from the gateway's
  `players` table. Op level, names or other claims from the mod don't grant anything.
- **Management accounts** are identified by name and SSH public keys.
- Linking an account to a player (`mcai link`, then `/ai link <code>` in game) only records who is who. It grants
  nothing, keeping gameplay, AI and infrastructure rights separate.
- **Changing roles.** You can only change someone below you, and only to a role below yours. Nobody changes
  their own role. Owners are made on the host: `MC_OWNER` at first join in online mode, or the SSH key in
  `data/gateway/bootstrap/owner.pub`. Anyone who can write there already controls the host.
- The permission matrix is in `src/mcai/rbac.py` (`mcai roles` prints it).

**SSH gateway** (`containers/management`):

- OpenSSH runs **unprivileged** in a read-only container with every capability dropped. It accepts keys only
  (`PasswordAuthentication no`, `UsePAM no`) and has a single login user.
- `ForceCommand` runs the dispatcher whatever the client asks for. `PermitTTY no`, `DisableForwarding yes` and
  no subsystems: no shell, no sftp, no tunnels.
- `ExposeAuthInfo` gives the dispatcher the key that authenticated. It forwards argv and that key to the
  gateway, which looks up the account on **every** command. A revoked key fails at once; sshd's
  `authorized_keys` is re-synced from the gateway every 5 s, so sshd also stops accepting it.
- The container has no Docker socket, no world data and no AI credential. Its port is published on 127.0.0.1
  unless the owner chooses the network during setup.
- `MaxAuthTries 3`, `LoginGraceTime 20` and `MaxStartups` limit guessing. Client alive checks end dead sessions;
  RPC sessions (the TUI) close after `MCAI_SSH_IDLE_TIMEOUT`.

**Client** (`management-cli/mcai_cli.py`): Python standard library plus the system `ssh`. Each server's host key
is **pinned** per remote, with `StrictHostKeyChecking=yes` and a separate `known_hosts`; `setup.sh` prints the
key. The client disables agent, X11 and port forwarding.

**Server lifecycle without the Docker socket.**

- `mcai server restart` asks Minecraft to stop over RCON (internal network only), and Docker's
  `restart: unless-stopped` starts it again.
- Stopping the whole stack, restores and upgrades stay on the host (`scripts/`). Exposing them needs a trusted
  host agent, which is deliberately not built yet.
- The gateway reads the server log and the backup folder through read-only mounts.

**Audit.**

- One JSON line per event, in UTC, one file per day (`data/gateway/audit/audit-YYYY-MM-DD.jsonl`, mode 600).
- Every record carries a hash of the previous one. `mcai audit verify` detects records that were changed,
  inserted or removed.
- Retention defaults to 365 days (`AUDIT_RETENTION_DAYS`). Export with `mcai audit show --format jsonl|csv`.
- What is recorded: SSH authentication results (with fingerprint and source IP), management commands that change
  something, every denial, role and key changes, player joins and leaves, AI requests and decisions, and audit
  reads.
- What is never recorded: keys, tokens, passwords, or players' request text (only its length and hash unless
  `AUDIT_LOG_TEXT=true`).
- IP addresses in server logs are masked for everyone below admin.

**Undo snapshots** are deleted when a build is undone and after `AI_UNDO_KEEP_DAYS` (default 7). The gateway
orders it and the mod deletes only files whose names are request ids.

## Consequences

- With one account per person, you get revocation and an audit trail per person. The dispatcher runs as the same
  Unix user as sshd, so a flaw in OpenSSH's forced-command handling would expose that user's files: the
  management token and the host key. It would not expose world data or the AI credential.
- Within a Docker NAT, the source IP of host-local connections is the bridge address, not 127.0.0.1.
- `MC_OWNER` bootstrap trusts Mojang-authenticated names, so it is disabled when `MC_ONLINE_MODE=false`.
- The vanilla ops list stays as it was (reset to the owner at start). Roles are what the platform checks;
  `/op` is never given to the AI.
