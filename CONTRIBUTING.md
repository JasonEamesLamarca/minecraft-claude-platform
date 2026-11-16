# Contributing

Thanks for your interest. The project is pre-alpha, so expect large changes. Check
[`docs/development/TASKS.md`](docs/development/TASKS.md) before starting on something big, and open an issue to
discuss it first.

## Ground rules

- **Never commit secrets, worlds or personal data.** That includes `.env`, API keys or tokens, SSH private keys,
  world folders, backups, databases, logs, real player names and UUIDs, and home IPs or hostnames. CI runs
  gitleaks on every push and PR. Install the pre-commit hook so problems are caught locally:
  `pipx install pre-commit && pre-commit install`.
- **Model output is untrusted input.** Any code path that turns model output or player text into a game action must
  go through the action schema and the allowlisted executor, never through a shell, RCON or raw console commands.
  Security tests must cover new actions.
- **No paid API calls in CI.** Tests use the mock AI provider.
- **Pin dependencies** (images by digest, GitHub Actions by commit SHA) and let Dependabot propose updates.
- **Architecture changes** need an ADR in `docs/architecture/adr/` (copy the format of an existing one).
- **Assets:** only original work or properly licensed material. No Mojang assets, and nothing based on other
  franchises.

## Workflow

1. Fork the repo, then branch from `main` (`feat/…`, `fix/…`, `docs/…`).
2. Keep PRs focused. Include tests and documentation updates.
3. Use [Conventional Commits](https://www.conventionalcommits.org/) messages (`feat:`, `fix:`, `docs:`, `chore:`…).
4. CI must pass before review.

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).
