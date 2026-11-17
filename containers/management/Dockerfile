# Minecraft AI Platform - SSH gateway (OpenSSH, keys only, forced command -> gateway management API).
# Built locally with the installing user's UID/GID: the non-root sshd can only log in the user it runs as,
# and the secret files it reads are owned by that user (mode 600).
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ARG PUID=1000
ARG PGID=1000
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
# openssh-server comes from Debian's security-maintained bookworm repository (rebuild to pick up fixes).
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-server \
 && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_* \
 && (getent group "${PGID}" >/dev/null || groupadd --gid "${PGID}" mcai) \
 && (getent passwd "${PUID}" >/dev/null && userdel "$(getent passwd "${PUID}" | cut -d: -f1)" || true) \
 && useradd --uid "${PUID}" --gid "${PGID}" --home-dir /data --no-create-home --shell /bin/sh mcai \
 && usermod -p '*' mcai \
 && mkdir -p /data /etc/mcai && chown "${PUID}:${PGID}" /data
COPY --chown=root:root containers/management/sshd_config /etc/mcai/sshd_config
COPY --chown=root:root containers/management/mcai-server /usr/local/bin/mcai-server
COPY --chown=root:root src/mcai /app/mcai
RUN chmod 755 /usr/local/bin/mcai-server && chmod 644 /etc/mcai/sshd_config
USER mcai
EXPOSE 2222
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python3", "-c", "import socket; socket.create_connection(('127.0.0.1', 2222), 3).recv(4)"]
ENTRYPOINT ["python3", "-m", "mcai.sshgw"]
CMD ["entrypoint"]
