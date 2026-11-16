# Developer guide

## Layout

| Path | What |
| --- | --- |
| `minecraft-mod/` | Fabric server mod (Java 21, Gradle + Fabric Loom, Yarn mappings) |
| `src/mcai/` | Gateway and AI worker (Python, standard library only) |
| `containers/*/Dockerfile` | Service images. The build context is the repo root, and `.dockerignore` only lets `src/mcai` in |
| `tests/unit`, `tests/integration` | pytest. No Docker, network or AI credentials needed (mock provider) |
| `tests/operational/first_start.sh` | End to end with real containers (needs Docker and internet, ~5 min) |

## Common tasks

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                         # unit + integration
scripts/build-mod.sh                     # build + test the mod in a container (no local Java needed)
tests/operational/first_start.sh         # full stack, in a temp copy
docker compose up -d --build gateway ai-worker   # after changing src/mcai
```

Mod changes: run `scripts/build-mod.sh`, then `docker compose up -d --force-recreate minecraft`.

## Updating pinned versions

Dependabot covers GitHub Actions, the images in `compose.yaml` and the Dockerfiles, Gradle and pip. Update these by hand:

- **Claude Code:** `ARG CLAUDE_CODE_VERSION` in `containers/claude/Dockerfile`. Pick a release that is at least two
  weeks old, rebuild, and run the tests.
- **Gradle build image:** the `IMAGE=` digest in `scripts/build-mod.sh`.
- **Fabric versions:** `minecraft-mod/gradle.properties` and `MC_*` in `.env.example`.

## Rules

See [CONTRIBUTING.md](../../CONTRIBUTING.md), the [threat model](../security/threat-model.md) and
[ADR-0006](../architecture/adr/0006-ai-gets-actions-not-shells.md). Anything a player or the model can reach needs
security tests.
