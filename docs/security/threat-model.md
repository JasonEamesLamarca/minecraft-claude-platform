# Threat model (v0, Phase 0)

STRIDE-style model of the planned architecture. Each mitigation will link to its tests once they exist. The model
is reviewed at every phase gate.

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
| T11 | Docker socket abuse | all | No socket in untrusted containers; lifecycle ops via a narrowly scoped helper with explicit authorization (design in Phase 4) |
| T12 | RCON exposure | 1 | RCON on internal network only, random password, never published |
| T13 | Supply-chain compromise | — | Images pinned by digest, actions pinned by SHA, Dependabot, Trivy, SBOM, least-privilege workflow tokens |
| T14 | Sensitive data in logs/repo | — | Structured logs with redaction; gitleaks + private deny-list in pre-commit and CI |
| T15 | Lost world (failed change, corruption) | — | Scheduled + pre-change backups, `save-off`/`save-all flush` around copies, restore tests |

## Residual risks (accepted, documented to users)

- A public game port is attackable through Minecraft server vulnerabilities themselves. Keep the image updated.
- The model may still produce unhelpful or odd-but-allowed actions within a player's budget and permissions.
- Host compromise exposes all secrets on it.
