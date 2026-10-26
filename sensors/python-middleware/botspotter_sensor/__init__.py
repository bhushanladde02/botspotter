"""botspotter-sensor: report AI-bot visits from your Python web app to BotSpotter.

Zero dependencies (stdlib only). Adds ~µs per request: it only records
header *names*, the User-Agent and the client IP, batches them in memory,
and ships them in a background thread every few seconds. Nothing blocks
your request path, and failures to reach BotSpotter are swallowed.

    # FastAPI / Starlette
    from botspotter_sensor import BotSpotterMiddleware
    app.add_middleware(BotSpotterMiddleware, key="YOUR_SENSOR_KEY")

    # Flask / any WSGI
    from botspotter_sensor import wsgi_sensor
    app.wsgi_app = wsgi_sensor(app.wsgi_app, key="YOUR_SENSOR_KEY")

    # Django: add "botspotter_sensor.django.BotSpotterMiddleware" to MIDDLEWARE
    # and BOTSPOTTER_KEY = "YOUR_SENSOR_KEY" in settings.py
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import deque

__version__ = "0.1.0"
DEFAULT_ENDPOINT = "https://botspotter.app/v1/ingest"


class Reporter:
    """Thread-safe batcher shared by all adapters."""

    def __init__(self, key: str, endpoint: str = DEFAULT_ENDPOINT, flush_every: float = 5.0, max_batch: int = 500):
        self.key, self.endpoint, self.flush_every, self.max_batch = key, endpoint, flush_every, max_batch
        self._buf: deque = deque(maxlen=10000)
        self._lock = threading.Lock()
        self._t = threading.Thread(target=self._loop, daemon=True, name="botspotter-sensor")
        self._t.start()

    def record(self, ua: str, header_names: list, ip: str, cf_country: str = "", method: str = "GET",
               status: int = 200, path: str = "", ja4: str = "") -> None:
        # Fast pre-filter: obvious browsers never leave the process.
        ual = (ua or "").lower()
        if "mozilla/" in ual and "accept-language" in (h.lower() for h in header_names) and "bot" not in ual \
                and "crawl" not in ual and "spider" not in ual and "headless" not in ual:
            return
        self._buf.append({"ts": time.time(), "ua": ua[:512], "headers": header_names[:40], "ip": ip,
                          "cf_country": cf_country, "method": method, "status": status,
                          "path": path if path.endswith("robots.txt") else "", "ja4": ja4})

    def flush(self) -> None:
        with self._lock:
            if not self._buf:
                return
            batch = [self._buf.popleft() for _ in range(min(len(self._buf), self.max_batch))]
        body = json.dumps({"events": batch}).encode()
        req = urllib.request.Request(self.endpoint, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-BS-Key": self.key,
                                              "User-Agent": f"botspotter-sensor/{__version__}"})
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass  # never let telemetry hurt the host app

    def _loop(self) -> None:
        while True:
            time.sleep(self.flush_every)
            self.flush()


def _client_ip(headers: dict) -> str:
    return (headers.get("cf-connecting-ip") or (headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or headers.get("x-real-ip") or "")


class BotSpotterMiddleware:
    """ASGI middleware (FastAPI, Starlette, Litestar, Quart…)."""

    def __init__(self, app, key: str, endpoint: str = DEFAULT_ENDPOINT, reporter: Reporter | None = None):
        self.app = app
        self.reporter = reporter or Reporter(key, endpoint)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        names = [k.decode("latin1") for k, _ in scope.get("headers", [])]
        hdrs = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        status_holder = {"status": 200}

        async def send_wrap(message):
            if message.get("type") == "http.response.start":
                status_holder["status"] = message.get("status", 200)
            await send(message)

        try:
            await self.app(scope, receive, send_wrap)
        finally:
            client = scope.get("client") or ("", 0)
            self.reporter.record(hdrs.get("user-agent", ""), names, _client_ip(hdrs) or client[0],
                                 hdrs.get("cf-ipcountry", ""), scope.get("method", "GET"),
                                 status_holder["status"], scope.get("path", ""), hdrs.get("x-bs-ja4", ""))


def wsgi_sensor(app, key: str, endpoint: str = DEFAULT_ENDPOINT, reporter: Reporter | None = None):
    """WSGI wrapper (Flask, Bottle, Pyramid, plain WSGI)."""
    rep = reporter or Reporter(key, endpoint)

    def wrapped(environ, start_response):
        status_holder = {"status": 200}

        def sr(status, headers, exc_info=None):
            status_holder["status"] = int(status.split(" ", 1)[0])
            return start_response(status, headers, exc_info)

        try:
            return app(environ, sr)
        finally:
            names = [k[5:].replace("_", "-").lower() for k in environ if k.startswith("HTTP_")]
            hdrs = {k[5:].replace("_", "-").lower(): v for k, v in environ.items() if k.startswith("HTTP_")}
            rep.record(environ.get("HTTP_USER_AGENT", ""), names, _client_ip(hdrs) or environ.get("REMOTE_ADDR", ""),
                       hdrs.get("cf-ipcountry", ""), environ.get("REQUEST_METHOD", "GET"), status_holder["status"],
                       environ.get("PATH_INFO", ""), hdrs.get("x-bs-ja4", ""))

    return wrapped
