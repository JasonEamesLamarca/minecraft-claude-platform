# Threat model (v3, Phase 4)

STRIDE-style model of the architecture. **Status** says what is implemented and tested, and what is still planned.
The model is reviewed at every phase gate.

## Assets

| Asset | Why it matters |
| --- | --- |
| AI credential (API key / subscription token) | Money, account abuse |
| Minecraft world + backups | Players' work; hard to recreate |
| RCON password, gateway mod token | Full or broad control of the server |
| Admin SSH keys / authorized_keys | Platform administration |
| Audit log, player identities (UUIDs, IPs) | Accountability; personal data |
| Host | Everything above |

## Actors

Anonymous internet user · authenticated player (untrusted text) · builder/operator · administrator/owner ·
the AI model (treated as able to be manipulated) · downloaded example content · supply chain (images, deps,
actions).

## Trust boundaries

1. Player client → minecraft server (game protocol)
2. minecraft mod → gateway (local API, token)
3. gateway ↔ ai-worker (job text in, proposals out)
4. ai-worker → AI provider (internet)
5. Admin → ssh-gateway (internet/LAN)
6. ssh-gateway → management API
7. Example download (GitHub release) → gateway

## Threats and planned mitigations

| # | Threat | Boundary | Mitigation (planned) |
| --- | --- | --- | --- |
| T1 | Prompt injection via chat/signs/books makes the AI grant op, run commands, read files | 1→3 | ADR-0006: no shell/file/network tools; MCP proposal tools only; executor allowlist; RBAC on the *requesting* UUID, not on model claims; untrusted text delimited |
| T2 | AI proposes a huge or destructive edit | 3 | Region bounds, block budgets, protected areas, preview + approval, snapshot + rollback |
| T3 | Excessive model usage / cost | 3,4 | Per-player and global rate limits, token/cost budgets, concurrency cap, timeouts, on-demand worker |
| T4 | Credential theft from containers | 4 | Docker secret mounted only into ai-worker; never in env of other services; not logged; non-root; read-only rootfs |
| T5 | Forged mod → gateway calls | 2 | Internal network only + per-install generated token; gateway never trusts player identity from anything but the mod |
| T6 | Player impersonation (names) | 1 | UUIDs authoritative; `online-mode=true` default; whitelist on by default |
| T7 | Automation loops / duplicate runs / AI-written destructive automations | 3 | Idempotency keys, recursion depth limit, rate caps, approval for AI-proposed or privileged automations, no custom code without admin deployment |
| T8 | SSH brute force / shell escape | 5 | Keys only, no passwords, `ForceCommand`, no PTY/forwarding/agent, `MaxAuthTries`, idle timeout, audit, key revocation |
| T9 | Privilege escalation via management API | 6 | Server-side RBAC per action; role changes are themselves privileged + audited; owner role not assignable via API by non-owners |
| T10 | Malicious example (path traversal, zip bomb, script, oversized) | 7 | URL allowlist, size caps, streaming hash verification, no archive extraction outside a temp dir, no script execution, schema-validated manifest, bounds check before placement |
| T11 | Docker socket abuse | all | No socket in any container. Minecraft restarts via RCON `stop` + Docker restart policy; stack lifecycle, restore and upgrades stay host-only scripts (ADR-0010) |
| T12 | RCON exposure | 1 | RCON on internal network only, random password, never published |
| T13 | Supply-chain compromise | — | Images pinned by digest, actions pinned by SHA, Dependabot, Trivy, SBOM, least-privilege workflow tokens |
| T14 | Sensitive data in logs/repo | — | Structured logs with redaction; gitleaks + private deny-list in pre-commit and CI |
| T15 | Lost world (failed change, corruption) | — | Scheduled + pre-change backups, `save-off`/`save-all flush` around copies, restore tests |

## Residual risks (accepted, documented to users)

- A public game port is attackable through Minecraft server vulnerabilities themselves. Keep the image updated.
- The model may still produce unhelpful or odd-but-allowed actions within a player's budget and permissions.
- Host compromise exposes all secrets on it.

## Implementation status (Phase 2)

| # | Status | Tests |
| --- | --- | --- |
| T1 | **Implemented for questions.** Claude runs with no tools at all (`--tools ""`, empty strict MCP config, 1 turn). Player text is cleaned and placed in a randomized delimiter. Replies are cleaned and shown as literal text, never parsed as commands | `test_ai_core.py` (command flags, delimiter, sanitizing), `test_gateway_flow.py::test_model_reply_sanitized_before_delivery` |
| T2 | **Implemented:** plans are data only (no NBT, commands or other namespaces), with a denied-block list, size, budget, distance and protected-area checks in the worker *and* the gateway. The mod re-parses blocks with NBT disabled. Builds need explicit approval from the requester or an admin, proposals expire, the area is snapshotted before building, and undo restores it. A broken protected-areas file blocks all builds (fail closed) | `test_plan.py` (52 tests incl. injection strings), `test_build_flow.py` (19 tests: approval rights, forged levels, expiry, worker bypass, restart), mod `PlanCursorTest`, operational test (build → verify → undo, TNT plan rejected) |
| T3 | **Implemented:** per-player rate limit, one pending request per player, queue cap, per-request `--max-budget-usd`, daily USD budget, timeouts | `test_rate_limit`, `test_one_pending_request_per_player`, `test_daily_budget`, `test_timeout_and_late_result_not_delivered` |
| T4 | **Implemented:** the credential is a secret mounted only into ai-worker and passed only in the `claude` child process environment. It is never in argv, logs or `.env` | `test_ai_credential_only_in_worker`, `test_claude_credential_only_in_child_env`, `test_api_key_goes_to_secret_file_not_env` |
| T5 | **Implemented:** separate mod and worker tokens, constant-time compare, failed attempts audited, internal network only | `test_mod_api_rejects_bad_or_worker_token`, `test_worker_api_rejects_mod_token` |
| T6 | Implemented: online-mode + whitelist, and the gateway accepts UUIDs only | Phase 1 tests, `test_malformed_requests_rejected` |
| T12 | Implemented (Phase 1) | `test_only_game_port_published`, operational test |
| T14 | **Implemented:** audit records store request length + hash, not text (`AUDIT_LOG_TEXT=false`). Structured JSON logs carry no secrets | `test_audit_log_has_no_request_text_by_default` |

New residual risks (Phase 2):
- The AI worker has general outbound internet access, not only Anthropic's API. An allowlisting egress proxy is
  planned for Phase 7.
- Prompt injection can still change *what text* the model answers, within the length limit. It can't make the
  platform do anything else.
- Subscription-token use carries terms-of-service risk for the server owner (ADR-0003).

New residual risks (Phase 3):
- An approved build replaces whatever is inside its bounding box, including other players' blocks. The approver
  sees the box (outline and coordinates) before approving, and undo restores it. Protected areas exclude
  important places.
- Undo snapshots are as sensitive as the world itself (they include container contents) and are stored in it.
- Interim authorization relies on vanilla op levels until RBAC (Phase 4).

## Implementation status (Phase 4: management, roles, audit)

| # | Status | Tests |
| --- | --- | --- |
| T6 | **Implemented:** roles are stored per player UUID. Op levels, names or roles claimed by the mod grant nothing. `MC_OWNER` becomes owner once, at first join, and only in online mode | `test_claims_from_the_mod_are_ignored`, `test_roles_by_uuid_not_name`, `test_owner_bootstrap_by_name_once`, `test_no_owner_bootstrap_in_offline_mode` |
| T8 | **Implemented:** OpenSSH, unprivileged, in a read-only container with every capability dropped. Keys only, `ForceCommand`, no PTY, forwarding, agent, X11, tunnels or subsystems, `MaxAuthTries 3`, `LoginGraceTime 20`, `MaxStartups`, client-alive and RPC idle timeouts. Bound to 127.0.0.1 by default. Every authentication result is audited with fingerprint and source IP. Revocation works immediately at the API and within 5 s at sshd. Clients pin the host key | `test_ssh_gateway_is_locked_down`, `test_authorized_keys_only_restricted_lines`, `test_authorized_keys_restricted_and_follow_revocation`, `test_cli.py`, operational test (no PTY, no port forward, no shell, revoked key refused by sshd); manual probes on 17 Nov 2026 (shell, `-tt bash`, `;`-injection, `-L` forward, sftp, password, root user, unknown key: all refused) |
| T9 | **Implemented:** every management command checks its permission in the gateway. You can only change people below you, and only to roles below yours. Nobody changes their own role, owners are made only on the host, and every denial is audited | `test_admin_escalation_attempts_fail` (5 cases), `test_auditor_is_read_only`, `test_operator_moderates_but_cannot_assign_roles`, `test_operator_cannot_kick_higher_role`, `test_may_assign` (11 cases) |
| T11 | **Implemented:** no Docker socket anywhere. `mcai server restart` = RCON `stop` + Docker restart policy | `test_no_docker_socket_mounted`, operational test mounts check; restart verified on the laptop (17 Nov 2026) |
| T12 | RCON stays internal; now also used by the gateway for allowlisted commands. Console commands are owner-only and cleaned to a single line | `test_console_command_is_owner_only_and_single_line` |
| T14 | **Implemented:** the audit log is one file per UTC day, hash-chained (`mcai audit verify`), mode 600, with retention (`AUDIT_RETENTION_DAYS`). Keys, tokens and passwords are never recorded. IP addresses in server logs are masked below admin | `test_audit_chain_detects_tampering`, `test_audit_chain_survives_a_second_writer`, `test_audit_never_contains_keys_or_tokens`, `test_logs_mask_ips_except_for_admins` |

New residual risks (Phase 4):
- The SSH dispatcher runs as the same Unix user as sshd. A flaw in OpenSSH's forced-command handling would expose
  the management token and the SSH host key. It would not expose world data, the RCON password or the AI
  credential, which aren't in that container.
- The management token lets the SSH gateway send any authenticated account's commands to the gateway, while the
  gateway checks each key. A compromised SSH gateway container could replay keys it has seen, so it can act as
  any account that has logged in.
- The gateway now holds the RCON password (full console). The gateway is the trust core (it already orders
  builds). It has no internet route and only reads, never writes, the logs and backups.
- The hash chain detects edits by anyone without write access to the gateway's data, and accidental damage. An
  attacker who controls the host can rewrite the whole chain. For that case, export the audit log off the host
  regularly (`mcai audit show --format jsonl`).
- Source IPs of host-local SSH connections show the Docker bridge address.
