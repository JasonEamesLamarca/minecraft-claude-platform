# Example automations

Each file is one automation. They are installed **disabled**: `mcai automations install <id>`, check it with
`mcai automations show <id>`, then `mcai automations enable <id>`. To change one, copy the file, edit it and
`mcai automations import <file>`. The format and every option are described in the
[administration guide](../docs/administration/README.md#automations).

| File | Trigger | What it does |
| --- | --- | --- |
| [welcome.toml](welcome.toml) | first join | greets the new player and tells everyone |
| [daily-announcement.toml](daily-announcement.toml) | 18:00 Berlin time, if someone is online | fixed announcement |
| [daily-ai-challenge.toml](daily-ai-challenge.toml) | 19:00 Fri–Sun, if someone is online | the AI invents a building challenge (max $0.10/day) |
| [lag-alert.toml](lag-alert.toml) | mspt above 45 for 60 s | warns online operators, logs it |
| [status-report.toml](status-report.toml) | every 6 h | status line into the audit log |
| [build-celebration.toml](build-celebration.toml) | an AI build finishes | announces it |
| [night-reminder.toml](night-reminder.toml) | night falls | reminds players to find shelter |
| [autosave.toml](autosave.toml) | every 30 min, if someone is online | flushes the world to disk |
| [promote-builders.toml](promote-builders.toml) | join, with 10 h of playtime | gives the builder role (**privileged**: the owner approves) |
| [spawn-tour.toml](spawn-tour.toml) | `/ai trigger tour` | a short guide in chat |
| [plaza-greeting.toml](plaza-greeting.toml) | entering the spawn plaza (edit the coordinates) | a hint about the tour |

Every example is validated by the test suite (`tests/unit/test_automation_defs.py`).
