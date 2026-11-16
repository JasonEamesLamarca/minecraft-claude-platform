"""AI worker: long-polls the gateway for jobs, answers them with the configured provider, posts results.

Holds the AI credential (from a Docker secret file); nothing else in the platform ever sees it.
Claude Code only runs while a job is being answered.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request

from . import __version__
from .config import env_float, env_int, read_secret, setup_logging
from .providers import ClaudeCodeProvider, MockProvider, NoneProvider, Provider

log = logging.getLogger("mcai.worker")


def make_provider() -> Provider:
    kind = os.environ.get("AI_PROVIDER", "claude").strip().lower()
    method = os.environ.get("AI_AUTH_METHOD", "none").strip().lower()
    if kind == "mock":
        return MockProvider()
    if kind != "claude":
        raise ValueError("AI_PROVIDER must be claude or mock")
    if method == "none":
        return NoneProvider()
    credential = read_secret(os.environ.get("AI_CREDENTIAL_FILE", "/run/secrets/ai_credential"))
    if not credential:
        log.warning("AI_AUTH_METHOD=%s but the credential file is empty: AI disabled", method)
        return NoneProvider()
    return ClaudeCodeProvider(method, credential, model=os.environ.get("AI_MODEL", "").strip(),
                              max_request_usd=env_float("AI_MAX_REQUEST_USD", 0.25, 0.01, 50.0),
                              max_build_usd=env_float("AI_MAX_BUILD_USD", 1.0, 0.01, 50.0),
                              build_max_turns=env_int("AI_BUILD_MAX_TURNS", 10, 2, 50),
                              mc_version=os.environ.get("MC_VERSION", "1.21.1"))


class GatewayAPI:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.token = token

    def _call(self, method: str, path: str, body: dict | None = None, timeout: float = 40) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "User-Agent": "mcai-worker"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def next_job(self, wait: int = 25) -> dict | None:
        return self._call("GET", f"/v1/worker/jobs?wait={wait}", timeout=wait + 15).get("job")

    def submit(self, request_id: str, result: dict) -> None:
        self._call("POST", f"/v1/worker/jobs/{request_id}/result", result, timeout=15)


def work_loop(api: GatewayAPI, provider: Provider, stop: threading.Event, wait: int = 25) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            job = api.next_job(wait)
            backoff = 1.0
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.warning("gateway unreachable (%s); retrying in %.0fs", e.__class__.__name__, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 30)
            continue
        if not job:
            continue
        rid = job["request_id"]
        t0 = time.monotonic()
        try:
            result = provider.run(job).as_dict()
        except Exception:
            log.exception("provider crashed on %s", rid)
            result = {"ok": False, "error": "provider crashed", "error_code": "provider_error", "provider": provider.name}
        log.info("job %s ok=%s provider=%s cost_usd=%.4f ms=%d", rid, result["ok"], result.get("provider"),
                 result.get("cost_usd", 0) or 0, int((time.monotonic() - t0) * 1000))
        for attempt in range(3):
            try:
                api.submit(rid, result)
                break
            except (urllib.error.URLError, OSError) as e:
                log.warning("could not post result for %s (%s), attempt %d", rid, e.__class__.__name__, attempt + 1)
                stop.wait(2 ** attempt)


def main() -> None:
    setup_logging("ai-worker")
    provider = make_provider()
    api = GatewayAPI(os.environ.get("GATEWAY_URL", "http://gateway:8080"),
                     read_secret(os.environ.get("WORKER_TOKEN_FILE", "/run/secrets/worker_token"), 32))
    concurrency = env_int("AI_MAX_CONCURRENT_JOBS", 2, 1, 16)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info("worker %s: provider=%s concurrency=%d", __version__, provider.name, concurrency)
    threads = [threading.Thread(target=work_loop, args=(api, provider, stop), daemon=True, name=f"job-{i}")
               for i in range(concurrency)]
    for t in threads:
        t.start()
    while not stop.wait(1):
        pass
    log.info("worker stopping")


if __name__ == "__main__":
    main()
