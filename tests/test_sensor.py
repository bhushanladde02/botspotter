"""The drop-in Python sensor: filters browsers locally, batches bots, posts them."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sensors" / "python-middleware"))
from botspotter_sensor import BotSpotterMiddleware, Reporter, wsgi_sensor  # noqa: E402

from .conftest import BOT_HEADERS, BROWSER_HEADERS, CHROME_UA, GPTBOT_UA  # noqa: E402


class CapturingReporter(Reporter):
    def __init__(self):
        self.posted: list[dict] = []
        self.key, self.endpoint, self.flush_every, self.max_batch = "k", "http://x", 999, 500
        from collections import deque
        import threading
        self._buf = deque(maxlen=10000)
        self._lock = threading.Lock()

    def flush(self):
        with self._lock:
            self.posted.extend(self._buf)
            self._buf.clear()


async def test_asgi_middleware_filters_browsers_and_keeps_bots():
    rep = CapturingReporter()
    app = FastAPI()

    @app.get("/")
    async def home():
        return {"hi": 1}

    app.add_middleware(BotSpotterMiddleware, key="k", reporter=rep)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://site") as c:
        r = await c.get("/", headers={"user-agent": CHROME_UA, "accept-language": "en"})
        assert r.status_code == 200
        r = await c.get("/robots.txt", headers={"user-agent": GPTBOT_UA, "x-forwarded-for": "20.171.1.1", "cf-ipcountry": "US"})
        assert r.status_code == 404  # route doesn't exist; sensor still records
    rep.flush()
    assert len(rep.posted) == 1
    ev = rep.posted[0]
    assert ev["ua"] == GPTBOT_UA and ev["ip"] == "20.171.1.1" and ev["cf_country"] == "US"
    assert ev["status"] == 404 and ev["path"] == "/robots.txt"
    assert "user-agent" in ev["headers"] and "host" in ev["headers"]


def test_wsgi_wrapper():
    rep = CapturingReporter()

    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    wrapped = wsgi_sensor(app, key="k", reporter=rep)
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/", "REMOTE_ADDR": "45.33.1.1",
           "HTTP_USER_AGENT": "python-requests/2.32", "HTTP_HOST": "site", "HTTP_ACCEPT_ENCODING": "gzip"}
    body = b"".join(wrapped(env, lambda s, h, e=None: None))
    assert body == b"ok"
    rep.flush()
    assert rep.posted[0]["ua"] == "python-requests/2.32" and rep.posted[0]["ip"] == "45.33.1.1"
    assert set(rep.posted[0]["headers"]) == {"user-agent", "host", "accept-encoding"}


def test_batch_shape_matches_ingest_contract():
    from botspotter.ingest.app import parse_batch

    rep = CapturingReporter()
    rep.record(GPTBOT_UA, BOT_HEADERS, "20.171.1.1", "US", "GET", 200, "/")
    rep.record(CHROME_UA, BROWSER_HEADERS, "1.1.1.1", "US", "GET", 200, "/")  # filtered client-side
    rep.flush()
    batch = parse_batch(json.dumps({"events": rep.posted}).encode())
    assert len(batch.events) == 1 and batch.events[0].ua == GPTBOT_UA
