"""Ingest service: the only thing sensors talk to.

POST /v1/ingest   (X-BS-Key)   batch of sensor hits  -> bot_events topic
POST /v1/beacon                portal page view      -> portal_visits topic
GET  /healthz

Does NOT touch Postgres or Redis. Auth, validate, enrich, publish, 202.
Stateless: run N replicas behind Nginx/Cloudflare.
"""
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager

import orjson
from fastapi import FastAPI, Header, HTTPException, Request, Response

from ..config import settings
from ..enrich import Enricher
from ..models import SensorBatch
from ..stream import make_stream

PORTAL_SITE = "botspotter-portal"


def client_ip(request: Request) -> str:
    h = request.headers
    return (h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else ""))


def parse_batch(body: bytes) -> SensorBatch:
    """Accept {"events":[...]}, a bare JSON array, or NDJSON (one hit per line)."""
    body = body.strip()
    if not body:
        return SensorBatch()
    if body[:1] == b"{" and b"\n{" not in body:
        data = orjson.loads(body)
        if "events" in data:
            return SensorBatch.model_validate(data)
        return SensorBatch(events=[data])
    if body[:1] == b"[":
        return SensorBatch(events=orjson.loads(body))
    return SensorBatch(events=[orjson.loads(line) for line in body.splitlines() if line.strip()])


def create_app(stream=None, enricher: Enricher | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.stream = stream or make_stream()
        app.state.enricher = enricher or Enricher()
        app.state.keys = settings.sensor_map()
        await app.state.stream.start()
        yield
        await app.state.stream.stop()

    app = FastAPI(title="BotSpotter ingest", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "service": "ingest"}

    @app.post("/v1/ingest", status_code=202)
    async def ingest(request: Request, x_bs_key: str = Header(default="")):
        site = request.app.state.keys.get(x_bs_key)
        if not site:
            # Also accept sha256-hashed keys from the sensors table in prod (see runbook).
            site = request.app.state.keys.get(hashlib.sha256(x_bs_key.encode()).hexdigest())
        if not site:
            raise HTTPException(401, "invalid sensor key")
        try:
            batch = parse_batch(await request.body())
        except Exception as exc:  # malformed JSON or schema
            raise HTTPException(400, f"bad batch: {exc.__class__.__name__}")
        enricher: Enricher = request.app.state.enricher
        st = request.app.state.stream
        accepted = bots = 0
        for hit in batch.events:
            accepted += 1
            # Sensors behind Cloudflare may not forward CF-IPCountry; fall back to ours.
            if not hit.cf_country:
                hit.cf_country = request.headers.get("cf-ipcountry", "")
            ev = enricher.bot_event(hit, site)
            if ev is None:
                continue
            bots += 1
            await st.publish(settings.topic_bot_events, ev.fp.encode(), ev.to_bytes())
        return {"accepted": accepted, "bots": bots, "site": site}

    @app.post("/v1/beacon", status_code=204)
    async def beacon(request: Request):
        """Called by the portal page's JS. Counts humans. If a bot calls it we
        still count it as a view — bots that run JS are rare and funny."""
        enricher: Enricher = request.app.state.enricher
        page, ref = "/", request.headers.get("referer", "")
        try:
            body = orjson.loads(await request.body() or b"{}")
            page = str(body.get("page", "/"))
            ref = str(body.get("ref") or ref)  # the page sends document.referrer (the HTTP Referer is the portal itself)
        except Exception:
            pass
        h = request.headers
        # City/region come from Cloudflare's free "Add visitor location headers" managed
        # transform (docs/runbook.rst); absent locally, which is fine.
        visit = enricher.portal_visit(
            client_ip(request), h.get("user-agent", ""), h.get("cf-ipcountry", ""), page, ref,
            city=h.get("cf-ipcity", ""), region=h.get("cf-region-code", ""), accept_language=h.get("accept-language", ""),
        )
        await request.app.state.stream.publish(settings.topic_portal_visits, visit.visitor.encode(), visit.to_bytes())
        return Response(status_code=204)

    return app


app = create_app()


def main() -> None:  # `botspotter-ingest`
    import uvicorn

    uvicorn.run("botspotter.ingest.app:app", host="0.0.0.0", port=8001, loop="uvloop", http="httptools")
