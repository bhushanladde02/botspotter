"""Public read API + the portal page.

GET /                      the portal (static, from web/)
GET /v1/snapshot           live counters (Cache-Control: max-age=2 -> CDN absorbs the crowd)
GET /v1/map?precision=2|3|4  geohash buckets for the map
GET /v1/bots/{slug}        one bot's profile
GET /v1/search?q=          name search (prefix, then substring)
GET /v1/feed               newly discovered bots
GET /v1/sightings          live ticker of recent hits (bot, site, country)
POST /v1/claims            start a claim (slug, email, method)  → token + instructions
GET  /v1/claims/{token}    claim status; returns owner_key once verified
POST /v1/claims/{token}/check   verify via /.well-known file
GET  /verify/{token}       the claimed BOT fetches this; fingerprint must match
PATCH /v1/bots/{slug}      X-BS-Owner: <owner_key> — rename / describe / homepage / operator / category
GET /v1/sensors            leaderboard of sites that caught the most bots
GET /healthz

Reads Redis only. Every request to this service is ALSO run through the
classifier: the portal is its own sensor, so bots that come to look at
themselves get counted under site "botspotter-portal".
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
import orjson
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ..config import settings
from ..claims import Claims
from ..counters import Counters
from ..enrich import Enricher
from ..models import SensorHit
from ..stream import make_stream

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
PORTAL_SITE = "botspotter-portal"


def create_app(counters: Counters | None = None, stream=None, enricher: Enricher | None = None,
               self_sensor: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.counters = counters or Counters.from_url()
        app.state.stream = stream or make_stream()
        app.state.enricher = enricher or Enricher()
        app.state.claims = Claims(app.state.counters)
        await app.state.stream.start()
        yield
        await app.state.stream.stop()

    app = FastAPI(title="BotSpotter API", lifespan=lifespan)
    ttl = settings.api_cache_ttl

    def cached(payload, max_age: int = ttl) -> Response:
        return Response(orjson.dumps(payload), media_type="application/json",
                        headers={"Cache-Control": f"public, max-age={max_age}, s-maxage={max_age}"})

    if self_sensor:
        @app.middleware("http")
        async def portal_sensor(request: Request, call_next):
            response = await call_next(request)
            try:
                h = request.headers
                ip = h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip() \
                    or (request.client.host if request.client else "")
                hit = SensorHit(ua=h.get("user-agent", ""), headers=list(h.keys()), ip=ip,
                                cf_country=h.get("cf-ipcountry", ""), ja4=h.get("x-bs-ja4", ""),
                                method=request.method, status=response.status_code, path=request.url.path)
                ev = request.app.state.enricher.bot_event(hit, PORTAL_SITE)
                if ev is not None:
                    await request.app.state.stream.publish(settings.topic_bot_events, ev.fp.encode(), ev.to_bytes())
            except Exception:  # never let the sensor break a page view
                pass
            return response

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "service": "api"}

    @app.get("/v1/snapshot")
    async def snapshot(request: Request):
        snap = await request.app.state.counters.read_snapshot()
        if snap is None:  # aggregator hasn't written one yet
            snap = await request.app.state.counters.build_snapshot()
        return cached(snap)

    @app.get("/v1/map")
    async def map_buckets(request: Request, precision: int = Query(3, ge=2, le=4), limit: int = Query(3000, le=10000)):
        return cached({"precision": precision, "buckets": await request.app.state.counters.map_buckets(precision, limit)}, 5)

    @app.get("/v1/bots/{slug}")
    async def bot(request: Request, slug: str):
        b = await request.app.state.counters.bot_by_slug(slug.lower())
        if b is None:
            raise HTTPException(404, "no bot by that name — yet")
        return cached(b, 10)

    @app.get("/v1/search")
    async def search(request: Request, q: str = Query(min_length=1, max_length=64), limit: int = Query(20, le=50)):
        return cached({"q": q, "results": await request.app.state.counters.search(q, limit)}, 10)

    @app.get("/v1/feed")
    async def feed(request: Request, limit: int = Query(50, le=200)):
        return cached({"feed": await request.app.state.counters.feed(limit)}, 5)

    @app.get("/v1/sightings")
    async def sightings(request: Request, limit: int = Query(50, le=100)):
        """Live ticker: the most recent bot hits across every sensor."""
        return cached({"sightings": await request.app.state.counters.sightings(limit)}, 2)

    # ------------------------------------------------------------ claims
    def _ip(request: Request) -> str:
        h = request.headers
        return (h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
                or (request.client.host if request.client else ""))

    @app.post("/v1/claims", status_code=201)
    async def create_claim(request: Request):
        try:
            body = orjson.loads(await request.body() or b"{}")
        except Exception:
            raise HTTPException(400, "bad json")
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "botspotter.app"
        scheme = "http" if host.startswith(("localhost", "127.")) else "https"
        res = await request.app.state.claims.create(str(body.get("slug", "")), str(body.get("email", "")),
                                                    str(body.get("method", "fetch")), _ip(request), f"{scheme}://{host}")
        return Response(orjson.dumps(res.body), status_code=res.status, media_type="application/json")

    @app.get("/v1/claims/{token}")
    async def claim_status(request: Request, token: str):
        res = await request.app.state.claims.status(token)
        return Response(orjson.dumps(res.body), status_code=res.status, media_type="application/json",
                        headers={"Cache-Control": "no-store"})

    @app.post("/v1/claims/{token}/check")
    async def claim_check_wellknown(request: Request, token: str):
        res = await request.app.state.claims.check_wellknown(token)
        return Response(orjson.dumps(res.body), status_code=res.status, media_type="application/json")

    @app.get("/verify/{token}")
    async def verify_fetch(request: Request, token: str):
        """The bot being claimed must fetch this. We fingerprint THIS request."""
        h = request.headers
        hit = SensorHit(ua=h.get("user-agent", ""), headers=list(h.keys()), ip=_ip(request),
                        cf_country=h.get("cf-ipcountry", ""), ja4=h.get("x-bs-ja4", ""), path=request.url.path)
        ev = request.app.state.enricher.bot_event(hit, PORTAL_SITE)
        res = await request.app.state.claims.verify_fetch(token, ev.fp if ev else None)
        return Response(orjson.dumps(res.body), status_code=res.status, media_type="application/json",
                        headers={"Cache-Control": "no-store"})

    @app.patch("/v1/bots/{slug}")
    async def update_bot(request: Request, slug: str):
        owner_key = request.headers.get("x-bs-owner", "")
        bot = await request.app.state.claims.authorize(slug, owner_key)
        if bot is None:
            raise HTTPException(403, "invalid owner key for this bot")
        try:
            patch = orjson.loads(await request.body() or b"{}")
        except Exception:
            raise HTTPException(400, "bad json")
        res = await request.app.state.claims.update(bot, patch)
        return Response(orjson.dumps(res.body), status_code=res.status, media_type="application/json")

    @app.get("/v1/sensors")
    async def sensors(request: Request, limit: int = Query(20, le=100)):
        return cached({"sensors": await request.app.state.counters.top_sensors(limit)}, 30)

    if WEB_DIR.exists():
        @app.get("/")
        async def index():
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "public, max-age=60"})

        @app.get("/about")
        @app.get("/sensors")
        @app.get("/terms")
        @app.get("/claim")
        async def static_page():
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "public, max-age=60"})

        @app.get("/bot/{slug}")
        async def bot_page(slug: str):
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "public, max-age=60"})

        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    return app


app = create_app()


def main() -> None:  # `botspotter-api`
    import uvicorn

    uvicorn.run("botspotter.api.app:app", host="0.0.0.0", port=8000, loop="uvloop", http="httptools")
