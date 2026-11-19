"""Egress proxy: only CONNECT to allowlisted host names on port 443, never to private addresses."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from mcai.egress import Proxy, check_target, parse_allow, public_address, serve  # noqa: E402

ALLOW = parse_allow("api.anthropic.com")


@pytest.mark.parametrize("target,ok", [
    ("api.anthropic.com:443", True),
    ("API.Anthropic.com.:443", True),
    ("api.anthropic.com:80", False),
    ("api.anthropic.com:8443", False),
    ("api.anthropic.com", False),
    ("evil.example:443", False),
    ("api.anthropic.com.evil.example:443", False),
    ("anthropic.com:443", False),
    ("1.2.3.4:443", False),
    ("[::1]:443", False),
    ("gateway:443", False),
    ("api.anthropic.com@evil.example:443", False),
    ("api anthropic.com:443", False),
])
def test_check_target(target, ok):
    host, reason = check_target(target, ALLOW)
    assert (host is not None) == ok, reason


def test_parse_allow_rejects_garbage():
    assert parse_allow(" api.anthropic.com , Example.org ") == {"api.anthropic.com", "example.org"}
    with pytest.raises(ValueError):
        parse_allow("api.anthropic.com,http://x")
    with pytest.raises(ValueError):
        parse_allow("*.anthropic.com")


@pytest.mark.parametrize("addr,ok", [("160.79.104.10", True), ("2607:6bc0::10", True), ("10.0.0.1", False),
                                     ("172.22.0.3", False), ("198.18.0.1", False), ("127.0.0.1", False),
                                     ("169.254.169.254", False), ("::1", False), ("fd00::1", False),
                                     ("100.64.0.1", False), ("0.0.0.0", False)])
def test_public_address(addr, ok):
    assert public_address(addr) == ok


async def _ask(port: int, request: bytes) -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(request)
    await w.drain()
    data = await asyncio.wait_for(r.read(4096), timeout=5)
    w.close()
    return data


def _run(coro):
    return asyncio.run(coro)


def _with_proxy(proxy, fn):
    async def go():
        server = await serve(proxy, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await fn(port)
        finally:
            server.close()
            await server.wait_closed()
    return _run(go())


def test_refuses_plain_http_and_other_hosts():
    proxy = Proxy(ALLOW)

    async def fn(port):
        get = await _ask(port, b"GET http://api.anthropic.com/ HTTP/1.1\r\nHost: api.anthropic.com\r\n\r\n")
        evil = await _ask(port, b"CONNECT evil.example:443 HTTP/1.1\r\n\r\n")
        ip = await _ask(port, b"CONNECT 160.79.104.10:443 HTTP/1.1\r\n\r\n")
        junk = await _ask(port, b"\x16\x03\x01garbage\r\n\r\n")
        return get, evil, ip, junk
    get, evil, ip, junk = _with_proxy(proxy, fn)
    assert get.startswith(b"HTTP/1.1 405")
    assert evil.startswith(b"HTTP/1.1 403")
    assert ip.startswith(b"HTTP/1.1 403")
    assert junk.startswith(b"HTTP/1.1 400")


def test_refuses_allowed_name_that_resolves_to_a_private_address():
    async def resolver(host):
        return ["172.22.0.3"]          # e.g. a poisoned name pointing at the gateway
    proxy = Proxy(ALLOW, resolver=resolver)
    out = _with_proxy(proxy, lambda port: _ask(port, b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n"))
    assert out.startswith(b"HTTP/1.1 403")


def test_oversized_header_is_refused():
    proxy = Proxy(ALLOW)
    out = _with_proxy(proxy, lambda port: _ask(port, b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nX: "
                                               + b"a" * 20000 + b"\r\n\r\n"))
    assert out.startswith(b"HTTP/1.1 400")


def test_tunnels_bytes_both_ways_to_an_allowed_host():
    async def go():
        async def echo(r, w):
            data = await r.read(100)
            w.write(b"pong:" + data)
            await w.drain()
            w.close()
        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        up_port = upstream.sockets[0].getsockname()[1]

        async def resolver(host):
            return ["127.0.0.1"]
        proxy = Proxy(ALLOW, resolver=resolver, allow_private=True, upstream_port=up_port)
        server = await serve(proxy, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com:443\r\n\r\n")
        await w.drain()
        status = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
        w.write(b"ping")
        await w.drain()
        reply = await asyncio.wait_for(r.read(100), 5)
        w.close()
        server.close()
        upstream.close()
        return status, reply
    status, reply = _run(go())
    assert status.startswith(b"HTTP/1.1 200")
    assert reply == b"pong:ping"


def test_tunnel_limit():
    async def go():
        hold = asyncio.Event()

        async def slow(r, w):
            await hold.wait()
            w.close()
        upstream = await asyncio.start_server(slow, "127.0.0.1", 0)
        up_port = upstream.sockets[0].getsockname()[1]

        async def resolver(host):
            return ["127.0.0.1"]
        proxy = Proxy(ALLOW, max_tunnels=1, resolver=resolver, allow_private=True, upstream_port=up_port)
        server = await serve(proxy, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        r1, w1 = await asyncio.open_connection("127.0.0.1", port)
        w1.write(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n")
        await w1.drain()
        first = await asyncio.wait_for(r1.readuntil(b"\r\n\r\n"), 5)
        second = await _ask(port, b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n")
        hold.set()
        w1.close()
        server.close()
        upstream.close()
        return first, second
    first, second = _run(go())
    assert first.startswith(b"HTTP/1.1 200")
    assert second.startswith(b"HTTP/1.1 503")
