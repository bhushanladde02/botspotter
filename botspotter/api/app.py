"""Public read API + the portal page.

GET /                      the portal (static, from web/)
GET /v1/snapshot           live counters (Cache-Control: max-age=2 -> CDN absorbs the crowd)
GET /v1/map?precision=2|3|4  geohash buckets for the map
GET /v1/bots/{slug}        one bot's profile
GET /v1/search?q=          name search (prefix, then substring)
GET /v1/feed               newly discovered bots
GET /v1/sightings          live ticker of recent hits (bot, site, country)
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
