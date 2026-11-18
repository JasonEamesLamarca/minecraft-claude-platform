# Minecraft AI Platform - integration gateway (Python standard library only, no third-party packages).
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin mcai \
 && mkdir -p /data && chown mcai:mcai /data
COPY --chown=root:root src/mcai /app/mcai
COPY --chown=root:root automation-examples /app/automation-examples
COPY --chown=root:root examples/catalog.json /app/examples/catalog.json
COPY --chown=root:root examples/atlantis/automations /app/examples/atlantis/automations
USER mcai
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python3", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["python3", "-m", "mcai.gateway"]
