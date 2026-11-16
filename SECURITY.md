# Security policy

## Supported versions

The project is pre-release. Until `v1.0.0`, only the latest release and `main` receive security fixes.

| Version | Supported |
| --- | --- |
| `main` | yes |
| latest `0.x` release | yes |
| older `0.x` releases | no |

## Reporting a vulnerability

Please **don't** open a public issue. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository.

Please include the affected component (mod, gateway, AI worker, automation, SSH gateway, management CLI, example
download), the version or commit, the steps to reproduce, and the impact. Prompt-injection paths that lead to
unauthorized game actions, file access or command execution count as vulnerabilities.

What to expect: an acknowledgement within 7 days and an assessment within 30 days. Fixes are coordinated
privately and credited in the advisory unless you ask otherwise.

## Scope and residual risk

This software runs a game server and an AI agent on your own hardware. It is designed to be secure by default (see
[`docs/security/threat-model.md`](docs/security/threat-model.md)), but it is not perfectly secure. In particular:

- An AI model can be manipulated by text players put into the game (chat, signs, books). The platform contains this
  by allowing only allowlisted, budgeted, permission-checked actions, not by trusting the model.
- Exposing the Minecraft port to the internet exposes you to the usual risks of public game servers. Use the
  whitelist.
- Your Anthropic credentials are only as safe as the host they are stored on.
