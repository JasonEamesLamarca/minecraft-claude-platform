# 0003 — AI authentication: API key or subscription token, user's choice

**Status:** Accepted (2026-11-16). The supported mechanisms must be re-verified against Anthropic's current
Claude Code documentation before the AI worker is implemented (Phase 2), and this ADR updated with the result.

**Context.** Claude Code can authenticate with an Anthropic API key (`ANTHROPIC_API_KEY`, billed per use) or with a
long-lived token created by `claude setup-token` from a Claude subscription (`CLAUDE_CODE_OAUTH_TOKEN`). Unattended,
programmatic use triggered by game events is a different usage pattern from an interactive developer session, and a
subscription does not automatically authorize every kind of automated use.

**Decision.** The setup wizard asks the user to choose:
1. **API key** (recommended for unattended workloads; clear per-use billing), or
2. **Subscription token**, with the documentation stating plainly that the user is responsible for complying with
   their plan's terms.

Either way: the credential is stored as a Docker secret readable only by the AI worker. It is never passed to the
Minecraft, gateway or SSH containers, and never logged. Token and cost budgets, rate limits and concurrency limits
apply regardless of the auth method. The interactive admin Claude session is separate from the unattended worker.

**Consequences.** Two code paths need to be documented and tested (CI tests both with the mock provider). The
provider interface stays abstract so other providers (e.g. a local Ollama model) can be added later.
