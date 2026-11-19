#!/usr/bin/env python3
"""mcai - administer a Minecraft AI Platform server over SSH (Linux, macOS, Windows).

Needs Python 3.9+ and the OpenSSH client (`ssh`; built into Windows 10+ and macOS). No other packages.

  mcai remote add home nas.local --port 2222 --host-key 'ssh-ed25519 AAAA...'   (once; key printed by setup.sh)
  mcai status
  mcai players list
  mcai players role Alex builder
  mcai logs -n 50          mcai logs --follow
  mcai automations import welcome.toml     (definition files are read here and sent to the server)
  mcai examples download atlantis-full      (downloads here, checks every checksum, sends it to the server)
  mcai help                (everything the server offers; what you may run depends on your role)

Local commands: remote add|list|remove|default, version, tui (full-screen console; once: mcai tui --install). Everything else runs on the server, which checks your
role for each command. The server's host key is pinned per remote (StrictHostKeyChecking=yes): a changed key
stops the connection instead of trusting it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

__version__ = "0.6.2"
MAX_FILE = 64 * 1024
FILE_COMMANDS = {("automations", "import"), ("automations", "validate"), ("auto", "import"), ("auto", "validate")}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
HOSTKEY_RE = re.compile(r"^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa) [A-Za-z0-9+/]+={0,3}$")


def config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "mcai"


class Config:
    def __init__(self, path: Path | None = None):
        self.dir = path or config_dir()
        self.file = self.dir / "config.json"
        self.known_hosts = self.dir / "known_hosts"
        self.data = {"default": None, "remotes": {}}
        if self.file.exists():
            self.data.update(json.loads(self.file.read_text(encoding="utf-8")))

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.dir, 0o700)
        self.file.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        lines = [f"mcai-{n} {r['host_key']}\n" for n, r in sorted(self.data["remotes"].items())]
        self.known_hosts.write_text("".join(lines), encoding="utf-8")

    def remote(self, name: str | None) -> tuple[str, dict]:
        name = name or os.environ.get("MCAI_REMOTE") or self.data.get("default")
        remotes = self.data["remotes"]
        if not name and len(remotes) == 1:
            name = next(iter(remotes))
        if not name or name not in remotes:
            raise SystemExit("No server configured. Add one first:\n  mcai remote add <name> <host> --port 2222 "
                             "--host-key '<key printed by setup.sh>'" if not remotes else
                             f"Unknown remote '{name}'. Known: {', '.join(sorted(remotes))}")
        return name, remotes[name]


def fingerprint(host_key: str) -> str:
    blob = base64.b64decode(host_key.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def ssh_binary() -> str:
    exe = shutil.which("ssh")
    if not exe:
        raise SystemExit("The OpenSSH client 'ssh' was not found. Windows: Settings > Apps > Optional features > "
                         "OpenSSH Client. Linux: install openssh-client.")
    return exe


def ssh_command(cfg: Config, name: str, r: dict, remote_argv: list[str]) -> list[str]:
    cmd = [ssh_binary(), "-T", "-p", str(r["port"]),
           "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", f"UserKnownHostsFile={cfg.known_hosts}", "-o", f"HostKeyAlias=mcai-{name}",
           "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30", "-o", "ForwardAgent=no",
           "-o", "ForwardX11=no", "-o", "ClearAllForwardings=yes", "-o", "LogLevel=ERROR"]
    if r.get("identity"):
        cmd += ["-i", r["identity"], "-o", "IdentitiesOnly=yes"]
    return cmd + [f"{r.get('user', 'mcai')}@{r['host']}", "--", " ".join(shlex.quote(a) for a in remote_argv)]


class Session:
    """One SSH session in RPC mode (JSON lines), for programs such as the TUI: call(argv) -> result dict."""

    def __init__(self, cfg: Config | None = None, remote: str | None = None):
        self.cfg = cfg or Config()
        self.name, r = self.cfg.remote(remote)
        self.proc = subprocess.Popen(ssh_command(self.cfg, self.name, r, ["rpc"]), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.n = 0

    def call(self, argv: list[str], input: str | None = None) -> dict:
        self.n += 1
        req = {"id": self.n, "argv": argv}
        if input is not None:
            req["input"] = input
        try:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        except (BrokenPipeError, OSError):
            line = ""
        if not line:
            err = self.proc.stderr.read() if self.proc.poll() is not None else ""
            return {"ok": False, "code": "disconnected", "text": (err.strip() or "Connection to the server closed.")}
        return json.loads(line)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


def file_argument(argv: list[str]) -> tuple[list[str], str | None]:
    """'automations import FILE' -> ('automations import --stdin', file text): the file is local, the server only
    receives its content."""
    if tuple(argv[:2]) not in FILE_COMMANDS:
        return argv, None
    rest = argv[2:]
    paths = [a for a in rest if not a.startswith("-")]
    if not paths:
        return argv, None
    path = Path(paths[0])
    try:
        data = path.read_bytes()
    except OSError as e:
        raise SystemExit(f"Can't read {path}: {e.strerror}") from None
    if len(data) > MAX_FILE:
        raise SystemExit(f"{path} is larger than {MAX_FILE // 1024} KB.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit(f"{path} is not UTF-8 text.") from None
    rest = [a for a in rest if a != paths[0]]
    return argv[:2] + ["--stdin"] + rest, text


# ------------------------------------------------------------------ example packs (downloaded here, not by the server)
# Same rules as the gateway (src/mcai/examples.py): this project's GitHub release downloads only, https, and the
# GitHub asset hosts only as redirect targets. The server has no internet access, so the client fetches.
DOWNLOAD_HOSTS = ("github.com",)
REDIRECT_HOSTS = ("objects.githubusercontent.com", "release-assets.githubusercontent.com")
RELEASE_PATH_RE = re.compile(r"^/[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}/releases/download/[A-Za-z0-9._+-]{1,100}/"
                             r"[A-Za-z0-9._-]{1,100}$")
PACK_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\.schem$")
MAX_DOWNLOAD = 8 * 1024 * 1024


def allowed_url(url: str, redirect: bool = False) -> bool:
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme != "https" or u.username or u.password or u.port not in (None, 443):
        return False
    if any(seg in (".", "..") for seg in u.path.split("/")):
        return False
    if redirect:
        return u.hostname in REDIRECT_HOSTS or (u.hostname in DOWNLOAD_HOSTS and bool(RELEASE_PATH_RE.match(u.path)))
    return u.hostname in DOWNLOAD_HOSTS and bool(RELEASE_PATH_RE.match(u.path)) and not u.query


def fetch(url: str, limit: int = MAX_DOWNLOAD) -> bytes:
    """Download with every redirect checked against the allowlist and a size cap."""
    import urllib.request

    class Guard(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not allowed_url(newurl, redirect=True):
                raise SystemExit(f"Refusing a redirect to {newurl.split('?')[0]} (not an allowed download host).")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    if not allowed_url(url):
        raise SystemExit(f"Refusing to download {url}: only this project's GitHub release files are allowed.")
    opener = urllib.request.build_opener(Guard)
    req = urllib.request.Request(url, headers={"User-Agent": f"mcai/{__version__}", "Accept": "application/octet-stream"})
    try:
        with opener.open(req, timeout=60) as r:
            data = r.read(limit + 1)
    except OSError as e:
        raise SystemExit(f"Download failed: {url} ({e})") from None
    if len(data) > limit:
        raise SystemExit(f"{url} is larger than {limit // (1024 * 1024)} MB.")
    return data


def _server_json(cfg: Config, name: str, r: dict, argv: list[str], data: bytes | None = None) -> dict:
    cmd = ssh_command(cfg, name, r, ["--json"] + argv)
    p = subprocess.run(cmd, input=base64.b64encode(data) if data is not None else b"", capture_output=True)
    try:
        return json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        err = p.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(err or "No answer from the server.") from None


def _local_file(src: Path, pid: str, name: str) -> bytes:
    """A pack file from a local folder: dist/examples (<pack>/<file>) or a release folder (flat)."""
    for p in (src / pid / name, src / name, src / f"{pid}-{name}" if name == "manifest.json" else src / "-"):
        if p.is_file():
            data = p.read_bytes()
            if len(data) > MAX_DOWNLOAD:
                raise SystemExit(f"{p} is larger than {MAX_DOWNLOAD // (1024 * 1024)} MB.")
            return data
    raise SystemExit(f"{name} of {pid} not found in {src}")


def examples_download(cfg: Config, remote: str | None, args: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="mcai examples download",
                                 description="Download an example pack, check it, and send it to the server.")
    ap.add_argument("pack")
    ap.add_argument("--version")
    ap.add_argument("--from", dest="source", help="use a local folder (e.g. dist/examples from "
                                                   "scripts/build-examples.sh) instead of the internet")
    ns = ap.parse_args(args)
    name, r = cfg.remote(remote)
    res = _server_json(cfg, name, r, ["examples", "catalog"])
    if not res.get("ok"):
        raise SystemExit(res.get("text", "The server refused the catalog request."))
    entries = [e for e in (res.get("data") or {}).get("catalog", []) if e.get("id") == ns.pack
               and (ns.version is None or e.get("version") == ns.version)]
    entry = entries[0] if entries else None
    if entry is None and not ns.source:
        raise SystemExit(f"'{ns.pack}' is not in the server's catalog (see 'mcai examples list').")
    if ns.source:
        src = Path(ns.source)
        manifest = _local_file(src, ns.pack, "manifest.json")
    else:
        manifest = fetch(entry["manifest_url"], 256 * 1024)
    sha = hashlib.sha256(manifest).hexdigest()
    official = entry is not None and sha == entry["manifest_sha256"]
    if not official and not ns.source:
        raise SystemExit(f"The downloaded manifest doesn't match the checksum the server expects for {ns.pack} "
                         f"({sha[:12]}... instead of {entry['manifest_sha256'][:12]}...). Nothing was sent.")
    if not official:
        print(f"Note: this is a local, unofficial build of {ns.pack} (its manifest isn't the one in the catalog). "
              f"The server checks it the same way and labels it unofficial.")
    try:
        m = json.loads(manifest)
        pid, ver, sections = m["id"], m["version"], m["sections"]
    except (ValueError, KeyError, TypeError):
        raise SystemExit("The manifest is not valid.") from None
    if pid != ns.pack or not all(isinstance(x, dict) and PACK_FILE_RE.match(str(x.get("file", ""))) for x in sections):
        raise SystemExit("The manifest doesn't describe this pack, or names files in a way that isn't allowed.")
    up = _server_json(cfg, name, r, ["examples", "upload", pid, ver, "manifest.json", "--stdin"], manifest)
    if not up.get("ok"):
        if up.get("code") == "exists":
            print(up.get("text"))
            return 0
        raise SystemExit(up.get("text", "The server refused the manifest."))
    print(up.get("text"))
    base = entry["manifest_url"].rsplit("/", 1)[0] if entry and not ns.source else None
    for i, sec in enumerate(sections, 1):
        data = _local_file(Path(ns.source), pid, sec["file"]) if ns.source else fetch(f"{base}/{sec['file']}")
        if hashlib.sha256(data).hexdigest() != sec.get("sha256") or len(data) != sec.get("bytes"):
            raise SystemExit(f"{sec['file']}: checksum or size doesn't match the manifest. Stopped; nothing of it "
                             f"was installed.")
        up = _server_json(cfg, name, r, ["examples", "upload", pid, ver, sec["file"], "--stdin"], data)
        if not up.get("ok"):
            raise SystemExit(up.get("text", f"The server refused {sec['file']}."))
        print(f"[{i}/{len(sections)}] {up.get('text')}", flush=True)
    print(f"Next: mcai examples info {pid}   then   mcai examples install {pid} --at X Y Z")
    return 0


# ------------------------------------------------------------------ the full-screen console
TEXTUAL = "textual==8.2.8"


def tui_python(cfg_dir: Path | None = None) -> Path:
    """Python of the console's own venv (created by 'mcai tui --install'), so the system Python stays untouched."""
    venv = (cfg_dir or config_dir()) / "venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install_tui() -> int:
    py = tui_python()
    venv = py.parent.parent
    print(f"Creating {venv} and installing {TEXTUAL} (once, about 10 MB)...")
    if subprocess.call([sys.executable, "-m", "venv", str(venv)]) != 0:
        print("Could not create the venv. On Debian/Ubuntu/Pop!_OS: sudo apt install python3-venv", file=sys.stderr)
        return 1
    if subprocess.call([str(py), "-m", "pip", "install", "--quiet", TEXTUAL]) != 0:
        return 1
    print("Done. Start the console with: mcai tui")
    return 0


def start_tui(cfg: Config, remote: str | None) -> int:
    try:
        import textual  # noqa: F401
    except ImportError:
        py = tui_python()
        if py.exists() and Path(sys.prefix).resolve() != py.parent.parent.resolve():   # not already in it
            here = os.path.join(os.path.dirname(os.path.realpath(__file__)), "mcai_cli.py")
            return subprocess.call([str(py), here, *(["-r", remote] if remote else []), "tui"])
        raise SystemExit("The console needs the Textual library. Install it once (into its own folder, your system "
                         "Python is not changed):\n  mcai tui --install") from None
    from mcai_tui import run as run_tui
    return run_tui(cfg, remote)


# ------------------------------------------------------------------ local commands
def cmd_remote(cfg: Config, argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="mcai remote", description="servers this computer administers")
    sub = p.add_subparsers(dest="action", required=True)
    a = sub.add_parser("add", help="add a server (pins its SSH host key)")
    a.add_argument("name")
    a.add_argument("host")
    a.add_argument("--port", type=int, default=2222)
    a.add_argument("--user", default="mcai")
    a.add_argument("--identity", help="private key file to use (default: your SSH keys/agent)")
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--host-key", help="the server's public host key, as printed by setup.sh")
    g.add_argument("--scan", action="store_true", help="fetch the key now and confirm its fingerprint yourself")
    sub.add_parser("list")
    rm = sub.add_parser("remove")
    rm.add_argument("name")
    d = sub.add_parser("default")
    d.add_argument("name")
    ns = p.parse_args(argv)
    remotes = cfg.data["remotes"]
    if ns.action == "list":
        if not remotes:
            print("No servers. mcai remote add <name> <host> --host-key '...'")
        for n, r in sorted(remotes.items()):
            star = "*" if n == cfg.data.get("default") else " "
            print(f"{star} {n:<12} {r.get('user', 'mcai')}@{r['host']}:{r['port']}  {fingerprint(r['host_key'])}")
        return 0
    if ns.action in ("remove", "default"):
        if ns.name not in remotes:
            raise SystemExit(f"Unknown remote '{ns.name}'.")
        if ns.action == "remove":
            del remotes[ns.name]
            if cfg.data.get("default") == ns.name:
                cfg.data["default"] = None
        else:
            cfg.data["default"] = ns.name
        cfg.save()
        return 0
    if not NAME_RE.match(ns.name):
        raise SystemExit("Remote names: letters, digits, - and _.")
    if not 1 <= ns.port <= 65535:
        raise SystemExit("--port must be 1-65535")
    if ns.scan:
        out = subprocess.run(["ssh-keyscan", "-T", "10", "-t", "ed25519", "-p", str(ns.port), ns.host],
                             capture_output=True, text=True)
        keys = [" ".join(line.split()[1:3]) for line in out.stdout.splitlines() if line and not line.startswith("#")]
        if not keys:
            raise SystemExit(f"Could not fetch a host key from {ns.host}:{ns.port}.")
        host_key = keys[0]
        print(f"The server at {ns.host}:{ns.port} presents host key fingerprint {fingerprint(host_key)}")
        print("Compare it with the server's: docker compose exec ssh-gateway ssh-keygen -lf /data/ssh_host_ed25519_key.pub")
        if input("Is it the same? Type 'yes' to trust it: ").strip() != "yes":
            raise SystemExit("Not added.")
    else:
        host_key = " ".join(ns.host_key.split()[:2])
    if not HOSTKEY_RE.match(host_key):
        raise SystemExit("--host-key must look like 'ssh-ed25519 AAAA...'")
    remotes[ns.name] = {"host": ns.host, "port": ns.port, "user": ns.user, "host_key": host_key,
                        **({"identity": str(Path(ns.identity).expanduser())} if ns.identity else {})}
    if not cfg.data.get("default"):
        cfg.data["default"] = ns.name
    cfg.save()
    print(f"Added {ns.name} ({fingerprint(host_key)}). Try: mcai{' -r ' + ns.name if cfg.data['default'] != ns.name else ''} whoami")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    remote, as_json = None, False
    while argv and argv[0] in ("-r", "--remote", "--json"):
        if argv[0] == "--json":
            as_json = True
            argv = argv[1:]
        else:
            if len(argv) < 2:
                raise SystemExit("--remote needs a name")
            remote, argv = argv[1], argv[2:]
    cfg = Config()
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    if argv[:1] == ["version"]:
        print(f"mcai {__version__}")
        return 0
    if argv[:1] == ["remote"]:
        return cmd_remote(cfg, argv[1:])
    if argv[:2] == ["tui", "--install"]:
        return install_tui()
    if argv[:1] == ["tui"]:
        return start_tui(cfg, remote)
    if argv[:2] == ["examples", "download"] and "-h" not in argv and "--help" not in argv:
        return examples_download(cfg, remote, argv[2:])
    name, r = cfg.remote(remote)
    argv, text = file_argument(argv)
    cmd = ssh_command(cfg, name, r, (["--json"] if as_json else []) + (argv or ["help"]))
    try:
        if text is None:
            return subprocess.call(cmd, stdin=subprocess.DEVNULL)
        return subprocess.run(cmd, input=text.encode("utf-8")).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
