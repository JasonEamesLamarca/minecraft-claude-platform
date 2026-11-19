"""Egress proxy for the AI worker: HTTPS tunnels (CONNECT) to allowlisted hosts only.

The AI worker has no route to the internet. Claude Code reaches the AI provider through this proxy
(HTTPS_PROXY), which:
  - accepts only `CONNECT host:443`, for host names on EGRESS_ALLOW (exact, case-insensitive);
  - refuses IP literals, other ports and plain HTTP requests;
  - resolves the name itself and refuses private, loopback, link-local and other non-public addresses, so a
    name can't be pointed at the platform's own services;
  - limits concurrent tunnels, header size and idle time, and logs every decision as one JSON line.
It never sees request contents: the tunnel carries TLS end to end.

Standard library only. Run: python3 -m mcai.egress
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import sys
import time

HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
DEFAULT_ALLOW = "api.anthropic.com"
MAX_HEADER = 8192


def log(event: str, **kw) -> None:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "svc": "egress", "event": event, **kw}
    print(json.dumps(rec, separators=(",", ":")), flush=True)


def parse_allow(value: str) -> frozenset[str]:
    hosts = set()
    for h in (value or "").split(","):
        h = h.strip().lower().rstrip(".")
        if h:
            if not HOST_RE.match(h):
                raise ValueError(f"EGRESS_ALLOW: not a host name: {h!r}")
            hosts.add(h)
    return frozenset(hosts)


def check_target(target: str, allow: frozenset[str]) -> tuple[str | None, str]:
    """`host:port` of a CONNECT line -> (host, "") when allowed, else (None, reason)."""
    host, sep, port = target.rpartition(":")
    if not sep or port != "443":
        return None, "only port 443"
    host = host.lower().rstrip(".")
    if host.startswith("["):
        return None, "IP addresses are not allowed"
    try:
        ipaddress.ip_address(host)
        return None, "IP addresses are not allowed"
    except ValueError:
        pass
    if not HOST_RE.match(host):
        return None, "bad host name"
    if host not in allow:
        return None, "host not on the allowlist"
    return host, ""


def public_address(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return ip.is_global and not ip.is_multicast


class Proxy:
    def __init__(self, allow: frozenset[str], max_tunnels: int = 32, idle_s: float = 300.0,
                 connect_timeout_s: float = 10.0, allow_private: bool = False, resolver=None,
                 upstream_port: int = 443):
        self.allow = allow
        self.slots = asyncio.Semaphore(max_tunnels)
        self.idle_s = idle_s
        self.connect_timeout_s = connect_timeout_s
        self.allow_private = allow_private          # tests only: lets a tunnel reach 127.0.0.1
        self.resolver = resolver or self._resolve
        self.upstream_port = upstream_port          # tests only: a local TLS-less echo server

    @staticmethod
    async def _resolve(host: str) -> list[str]:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return [i[4][0] for i in infos]

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = (writer.get_extra_info("peername") or ("?",))[0]
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
                return await self._reply(writer, 400, "Bad Request")
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = line.split(" ")
            if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
                return await self._reply(writer, 400, "Bad Request")
            method, target = parts[0], parts[1]
            if method != "CONNECT":
                log("denied", peer=peer, method=method[:16], reason="only CONNECT (HTTPS) is allowed")
                return await self._reply(writer, 405, "Method Not Allowed")
            host, reason = check_target(target, self.allow)
            if host is None:
                log("denied", peer=peer, target=target[:300], reason=reason)
                return await self._reply(writer, 403, "Forbidden")
            if self.slots.locked():
                log("denied", peer=peer, host=host, reason="too many tunnels")
                return await self._reply(writer, 503, "Service Unavailable")
            async with self.slots:
                await self._tunnel(reader, writer, peer, host)
        finally:
            if not writer.is_closing():
                writer.close()

    async def _tunnel(self, reader, writer, peer: str, host: str) -> None:
        try:
            addrs = await asyncio.wait_for(self.resolver(host), timeout=self.connect_timeout_s)
        except Exception:
            log("denied", peer=peer, host=host, reason="name does not resolve")
            return await self._reply(writer, 502, "Bad Gateway")
        if not self.allow_private:
            addrs = [a for a in addrs if public_address(a)]
        if not addrs:
            log("denied", peer=peer, host=host, reason="resolves to a non-public address")
            return await self._reply(writer, 403, "Forbidden")
        try:
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection(addrs[0], self.upstream_port),
                                                self.connect_timeout_s)
        except Exception:
            log("failed", peer=peer, host=host, reason="upstream connection failed")
            return await self._reply(writer, 502, "Bad Gateway")
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        log("tunnel", peer=peer, host=host, addr=addrs[0])
        start, sent = time.monotonic(), [0, 0]
        await asyncio.gather(self._pipe(reader, up_w, sent, 0), self._pipe(up_r, writer, sent, 1))
        log("closed", peer=peer, host=host, up_bytes=sent[0], down_bytes=sent[1],
            seconds=round(time.monotonic() - start, 1))

    async def _pipe(self, src, dst, sent, i) -> None:
        try:
            while True:
                data = await asyncio.wait_for(src.read(65536), timeout=self.idle_s)
                if not data:
                    break
                sent[i] += len(data)
                dst.write(data)
                await dst.drain()
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            if not dst.is_closing():
                dst.close()

    @staticmethod
    async def _reply(writer, code: int, text: str) -> None:
        try:
            writer.write(f"HTTP/1.1 {code} {text}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
            await writer.drain()
        except (ConnectionError, OSError):
            pass


async def serve(proxy: Proxy, host: str, port: int) -> asyncio.base_events.Server:
    return await asyncio.start_server(proxy.handle, host, port, limit=MAX_HEADER)


def main() -> None:
    try:
        allow = parse_allow(os.environ.get("EGRESS_ALLOW", DEFAULT_ALLOW))
    except ValueError as e:
        print(f"egress: {e}", file=sys.stderr)
        sys.exit(2)
    port = int(os.environ.get("EGRESS_PORT", "3128"))
    proxy = Proxy(allow, max_tunnels=int(os.environ.get("EGRESS_MAX_TUNNELS", "32")))

    async def run():
        server = await serve(proxy, "0.0.0.0", port)
        log("ready", port=port, allow=sorted(allow))
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
