"""SSH gateway container: OpenSSH (keys only, forced command) in front of the gateway's management API.

  python -m mcai.sshgw entrypoint   supervise sshd, sync authorized_keys from the gateway, report auth events
  python -m mcai.sshgw hostkey      create the host key if missing and print its public half (setup.sh)
  python -m mcai.sshgw dispatch     the forced command run for every SSH login (never a shell)

sshd runs as the unprivileged service user; every login runs `dispatch`, whatever command the client asked for.
dispatch sends argv plus the key that authenticated (sshd's ExposeAuthInfo) to the gateway, which looks up the
account and decides. This container holds no Docker socket, no world data and no AI credential.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA = Path(os.environ.get("SSHGW_DATA_DIR", "/data"))
HOST_KEY = DATA / "ssh_host_ed25519_key"
AUTH_KEYS = DATA / "authorized_keys"
RUNTIME = Path("/tmp/mcai-sshgw.json")          # settings for dispatch (sshd gives sessions a clean environment)
MAX_COMMAND = 8192
MAX_INPUT = 64 * 1024                           # a file sent with --stdin (automation definitions)
MAX_UPLOAD = 12 * 1024 * 1024                   # 'examples upload': one pack file, base64 (EXAMPLES_MAX_FILE_MB <= 8)
MAX_RPC_LINE = MAX_INPUT + MAX_COMMAND + 1024

LOG_PATTERNS = [
    (re.compile(r"^Accepted publickey for (\S+) from (\S+) port \d+ \S+: \S+ (SHA256:\S+)"), "accepted"),
    (re.compile(r"^Failed publickey for (\S+) from (\S+) port \d+ \S+: \S+ (SHA256:\S+)"), "key_rejected"),
    (re.compile(r"^Invalid user (\S*) from (\S+) port \d+"), "invalid_user"),
    (re.compile(r"^Connection closed by authenticating user (\S+) (\S+) port \d+ \[preauth\]"), "closed_preauth"),
    (re.compile(r"^Disconnecting authenticating user (\S+) (\S+) port \d+: Too many authentication failures"),
     "too_many"),
    (re.compile(r"^Disconnected from user (\S+) (\S+) port \d+"), "disconnected"),
]


def parse_sshd_line(line: str) -> dict | None:
    """An sshd log line -> auth event for the audit log, or None."""
    line = re.sub(r"^\S+\[\d+\]: ", "", line.strip())        # optional "sshd[12]: " prefix
    for rx, result in LOG_PATTERNS:
        m = rx.match(line)
        if m:
            ev = {"result": result, "user": m.group(1), "source_ip": m.group(2)}
            if m.lastindex and m.lastindex >= 3:
                ev["fingerprint"] = m.group(3)
            return ev
    return None


class Api:
    def __init__(self, url: str, token: str, timeout: float = 30):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except ValueError:
                return {"ok": False, "code": "http", "text": f"gateway error {e.code}"}


def _token() -> str:
    return Path(os.environ.get("MANAGEMENT_TOKEN_FILE", "/run/secrets/management_token")).read_text().strip()


# ------------------------------------------------------------------ host key
def ensure_host_key() -> str:
    if not HOST_KEY.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "mcai-ssh-gateway", "-f", str(HOST_KEY)],
                       check=True)
    os.chmod(HOST_KEY, 0o600)
    return Path(str(HOST_KEY) + ".pub").read_text().strip()


# ------------------------------------------------------------------ entrypoint
def write_authorized_keys(lines: list[str]) -> bool:
    """Atomically replace authorized_keys if it changed. Every line must carry `restrict`."""
    content = "".join(line + "\n" for line in lines if line.startswith("restrict ") and "\n" not in line)
    if AUTH_KEYS.exists() and AUTH_KEYS.read_text() == content:
        return False
    tmp = AUTH_KEYS.with_suffix(".tmp")
    tmp.write_text(content)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTH_KEYS)
    return True


def sync_keys(api: Api, stop: threading.Event, interval: float) -> None:
    while not stop.is_set():
        try:
            r = api.call("GET", "/v1/mgmt/authorized-keys")
            if isinstance(r.get("keys"), list) and write_authorized_keys([str(k) for k in r["keys"]]):
                log(f"authorized_keys updated ({len(r['keys'])} keys)")
        except (OSError, ValueError) as e:
            log(f"key sync failed: {e.__class__.__name__}")
        stop.wait(interval)


def log(msg: str) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "service": "ssh-gateway", "msg": msg}),
          flush=True)


def entrypoint() -> int:
    url = os.environ.get("GATEWAY_URL", "http://gateway:8080")
    api = Api(url, _token(), timeout=10)
    RUNTIME.write_text(json.dumps({"gateway_url": url,
                                   "idle_timeout_s": int(os.environ.get("MCAI_SSH_IDLE_TIMEOUT", "1800"))}))
    os.chmod(RUNTIME, 0o600)
    pub = ensure_host_key()
    log(f"host key: {pub}")
    if not AUTH_KEYS.exists():
        write_authorized_keys([])
    stop = threading.Event()
    threading.Thread(target=sync_keys, args=(api, stop, float(os.environ.get("MCAI_SSH_KEY_SYNC_SECONDS", "5"))),
                     daemon=True).start()
    proc = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", "/etc/mcai/sshd_config"],
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: proc.terminate())
    for line in proc.stderr:                     # sshd's log: forward to docker logs + audit auth events
        sys.stdout.write(line)
        sys.stdout.flush()
        ev = parse_sshd_line(line)
        if ev:
            try:
                api.call("POST", "/v1/mgmt/ssh-event", ev)
            except OSError:
                pass
    stop.set()
    return proc.wait()


# ------------------------------------------------------------------ dispatch (forced command)
def _client_key() -> str:
    """The public key this session authenticated with (sshd ExposeAuthInfo)."""
    p = os.environ.get("SSH_USER_AUTH", "")
    try:
        for line in Path(p).read_text().splitlines():
            if line.startswith("publickey "):
                return line[len("publickey "):].strip()
    except OSError:
        pass
    return ""


def dispatch() -> int:
    cfg = json.loads(RUNTIME.read_text()) if RUNTIME.exists() else {}
    api = Api(cfg.get("gateway_url", "http://gateway:8080"), _token())
    key = _client_key()
    source_ip = (os.environ.get("SSH_CONNECTION", "").split() or [""])[0]
    session = secrets.token_hex(6)
    raw = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if len(raw) > MAX_COMMAND:
        print("Command too long.", file=sys.stderr)
        return 2
    try:
        argv = shlex.split(raw)
    except ValueError:
        print("Could not parse the command (unbalanced quotes?).", file=sys.stderr)
        return 2
    base = {"key": key, "source_ip": source_ip, "session": session}
    upload = (argv[1:3] if argv[:1] == ["--json"] else argv[:2]) == ["examples", "upload"]
    if "--stdin" in argv:
        cap = MAX_UPLOAD if upload else MAX_INPUT
        data = sys.stdin.read(cap + 1)
        if len(data) > cap:
            print(f"Input larger than {cap // 1024} KB.", file=sys.stderr)
            return 2
        base["input"] = data
    if argv == ["rpc"]:
        return rpc(api, base, cfg.get("idle_timeout_s", 1800))
    as_json = bool(argv) and argv[0] == "--json"
    if as_json:
        argv = argv[1:]
    follow = "logs" in argv[:1] and ("--follow" in argv or "-f" in argv)
    if follow:
        argv = [a for a in argv if a not in ("--follow", "-f")]
        return follow_logs(api, base, argv, as_json)
    try:
        r = api.call("POST", "/v1/mgmt/upload" if upload else "/v1/mgmt/run", {**base, "argv": argv})
    except OSError:
        print("The management service is not reachable (is the gateway running?).", file=sys.stderr)
        return 3
    out = json.dumps(r) if as_json else r.get("text", "")
    if out:
        print(out, file=sys.stdout if r.get("ok") or as_json else sys.stderr)
    return 0 if r.get("ok") else (4 if r.get("code") in ("forbidden", "unknown_key") else 1)


def follow_logs(api: Api, base: dict, argv: list[str], as_json: bool) -> int:
    cursor = ""
    while True:
        try:
            r = api.call("POST", "/v1/mgmt/run", {**base, "argv": argv + (["--after", cursor] if cursor else [])})
        except OSError:
            time.sleep(3)
            continue
        if not r.get("ok"):
            print(r.get("text", "error"), file=sys.stderr)
            return 4 if r.get("code") in ("forbidden", "unknown_key") else 1
        data = r.get("data") or {}
        for line in data.get("lines", []):
            print(json.dumps({"line": line}) if as_json else line, flush=True)
        cursor = data.get("cursor", "")
        time.sleep(1)


def rpc(api: Api, base: dict, idle_timeout: int) -> int:
    """JSON lines over one SSH session (the TUI): {"id", "argv"} in, {"id", ...result} out. Ends after
    `idle_timeout` seconds without a request."""
    import select
    started = time.time()
    try:
        api.call("POST", "/v1/mgmt/session", {**base, "event": "start", "mode": "rpc"})
    except OSError:
        pass
    try:
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], idle_timeout)
            if not ready:
                print(json.dumps({"id": None, "ok": False, "code": "idle", "text": "Session closed after inactivity."}),
                      flush=True)
                return 0
            line = sys.stdin.readline()
            if not line:
                return 0
            if len(line) > MAX_RPC_LINE:
                print(json.dumps({"id": None, "ok": False, "code": "usage", "text": "Request too long."}), flush=True)
                continue
            try:
                req = json.loads(line)
                rid, argv = req.get("id"), req["argv"]
            except (ValueError, KeyError, TypeError, AttributeError):
                print(json.dumps({"id": None, "ok": False, "code": "usage", "text": "Bad request."}), flush=True)
                continue
            body = {**base, "argv": argv}
            if isinstance(req.get("input"), str) and len(req["input"]) <= MAX_INPUT:
                body["input"] = req["input"]
            try:
                r = api.call("POST", "/v1/mgmt/run", body)
            except OSError:
                r = {"ok": False, "code": "unavailable", "text": "The management service is not reachable."}
            print(json.dumps({"id": rid, **r}), flush=True)
            if r.get("code") == "unknown_key":
                return 4
    finally:
        try:
            api.call("POST", "/v1/mgmt/session", {**base, "event": "end", "mode": "rpc",
                                                  "duration_s": time.time() - started})
        except OSError:
            pass


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "entrypoint":
        return entrypoint()
    if cmd == "hostkey":
        print(ensure_host_key())
        return 0
    if cmd == "dispatch":
        return dispatch()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
