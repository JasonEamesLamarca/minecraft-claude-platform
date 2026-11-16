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

## Verification (2026-11-16, before Phase 2)

Checked against Anthropic's Claude Code documentation (code.claude.com/docs/en/setup, /iam, /legal-and-compliance,
/cli-reference):

- **Both mechanisms exist as described.** `ANTHROPIC_API_KEY` is always used in `-p` mode when present.
  `claude setup-token` creates a one-year OAuth token, used as `CLAUDE_CODE_OAUTH_TOKEN` and documented "for CI
  pipelines and scripts".
- **Anthropic's terms are more restrictive than this ADR first assumed.** The legal page says subscription OAuth is
  "designed to support ordinary use of Claude Code". It says developers building products or services "should use
  API key authentication". It says third parties may not "route requests through Free, Pro, Max, or Ultra plan
  credentials on behalf of their users".
- **Owner decision:** keep both options, as originally decided. Consequences in the implementation:
  - The wizard recommends the API key and shows a terms notice, quoting the page, whenever the subscription
    option is chosen.
  - The platform only stores the token the server owner enters on their own machine. It never handles anyone
    else's credentials.
  - Budgets and rate limits apply to both methods.
  - Revisit this ADR if Anthropic's terms change or before any hosted offering.
- **Installation:** the worker image installs `@anthropic-ai/claude-code` at an exact version with npm and sets
  `DISABLE_AUTOUPDATER=1` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, as the dev container docs recommend
  for pinning.
