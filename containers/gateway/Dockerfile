# Minecraft AI Platform - integration gateway (Python standard library only, no third-party packages).
FROM python:3.15-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin mcai \
 && mkdir -p /data && chown mcai:mcai /data
COPY --chown=root:root src/mcai /app/mcai
USER mcai
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python3", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["python3", "-m", "mcai.gateway"]
