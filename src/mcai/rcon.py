"""Minimal Source RCON client (what Minecraft speaks), used by the gateway for management commands.

RCON is only reachable on the internal Docker network. Commands come from the management API's allowlist
(whitelist, kick, say, save, restart) or, for the owner only, the raw console command.
"""
from __future__ import annotations

import socket
import struct
import threading

AUTH, EXEC, RESPONSE = 3, 2, 0


class RconError(Exception):
    pass


def clean_command(cmd: str, limit: int = 256) -> str:
    """One line of printable text; RCON has no quoting, so newlines and control characters are dropped."""
    out = "".join(ch for ch in str(cmd) if ch.isprintable()).strip()
    if not out:
        raise RconError("empty command")
    if len(out) > limit:
        raise RconError(f"command longer than {limit} characters")
    return out


class Rcon:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self._lock = threading.Lock()

    @staticmethod
    def _send(sock: socket.socket, rid: int, kind: int, body: str) -> None:
        payload = struct.pack("<ii", rid, kind) + body.encode("utf-8") + b"\x00\x00"
        sock.sendall(struct.pack("<i", len(payload)) + payload)

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise RconError("connection closed")
            buf += chunk
        return buf

    def _recv(self, sock: socket.socket) -> tuple[int, int, str]:
        (size,) = struct.unpack("<i", self._recv_exact(sock, 4))
        if not 10 <= size <= 1 << 20:
            raise RconError("bad packet")
        data = self._recv_exact(sock, size)
        rid, kind = struct.unpack("<ii", data[:8])
        return rid, kind, data[8:-2].decode("utf-8", "replace")

    def command(self, cmd: str) -> str:
        cmd = clean_command(cmd)
        with self._lock, socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            self._send(sock, 1, AUTH, self.password)
            rid, kind, _ = self._recv(sock)
            if kind != 2:            # some servers send an empty RESPONSE first
                rid, kind, _ = self._recv(sock)
            if rid == -1:
                raise RconError("RCON authentication failed")
            self._send(sock, 2, EXEC, cmd)
            _, _, body = self._recv(sock)    # answers longer than 4096 bytes are cut off (fine for our commands)
            return body
