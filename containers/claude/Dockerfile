# Minecraft AI Platform - AI worker: Claude Code (pinned) + the worker loop (Python standard library).
# Claude Code runs only while answering a job, with every tool removed (see src/mcai/providers.py).
FROM node:26-bookworm-slim@sha256:5df79989ed685e09f0a98019db4cebfb22fb9df1a53c0161664a011f7ff6b0f1
ARG CLAUDE_CODE_VERSION=2.1.316
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# Pinned per the Claude Code dev container docs (npm install with an exact version + updater disabled).
RUN npm install -g --no-fund --no-audit "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force \
 && claude --version
ENV DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    HOME=/home/worker \
    CLAUDE_CONFIG_DIR=/home/worker/.claude \
    TMPDIR=/tmp \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY --chown=root:root src/mcai /app/mcai
# The node image's "node" user (uid 1000) runs the worker; HOME and /tmp are tmpfs in compose.
USER node
ENTRYPOINT ["python3", "-m", "mcai.worker"]
