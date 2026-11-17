# mcai: command-line administration

`mcai` administers a Minecraft AI Platform server over SSH from Linux, macOS or Windows. It needs Python 3.9+
and the OpenSSH client (`ssh`), which is built into Windows 10+ and macOS. It has no other dependencies.

```bash
management-cli/mcai remote add home nas.local --port 2222 --host-key 'ssh-ed25519 AAAA...'   # printed by setup.sh
management-cli/mcai whoami
management-cli/mcai status
management-cli/mcai help
```

- **Windows:** `management-cli\mcai.cmd status`.
- **Install as a command:** `pip install ./management-cli` (or `pipx install ./management-cli`), then `mcai status`.
- **Settings:** stored in `~/.config/mcai/` (`%APPDATA%\mcai` on Windows): `config.json` holds the servers, and
  `known_hosts` holds their pinned host keys.
- **Choosing a server:** `mcai -r <name> ...` picks one; `mcai remote default <name>` sets the default.
- **Choosing a key:** `--identity` on `remote add` uses a specific private key; otherwise your normal SSH keys
  and agent are used.

**Full-screen console:** `pip install textual`, then run `mcai tui`. It shows a status bar (TPS, heap, players, AI
budget, your role), the live server log with a command line for any `mcai` command, the player list
(Enter: info, role, kick, whitelist) and the AI requests waiting for a decision (Enter: details, approve,
cancel). It uses one SSH session and closes after 30 minutes without activity.

The server decides what you may do, according to your account's role. See
[Remote administration and roles](../docs/administration/README.md#remote-administration-and-roles).
