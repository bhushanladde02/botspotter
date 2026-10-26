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

import hmac
import time

import orjson
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ..config import settings
from ..avatar import avatar_svg
from ..audience import build_ledger, ledger_csv, visits_csv
from ..claims import Claims
from ..counters import Counters
from ..enrich import Enricher
from ..models import SensorHit
from ..store import make_store
from ..stream import make_stream

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
PORTAL_SITE = "botspotter-portal"


def create_app(counters: Counters | None = None, stream=None, enricher: Enricher | None = None,
               self_sensor: bool = True, store=None, admin_key: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.counters = counters or Counters.from_url()
        app.state.stream = stream or make_stream()
        app.state.store = store or make_store()
        app.state.enricher = enricher or Enricher()
        app.state.claims = Claims(app.state.counters)
        app.state.admin_key = settings.admin_key if admin_key is None else admin_key
        await app.state.stream.start()
        await app.state.store.start()
        yield
        await app.state.store.stop()
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

    # ------------------------------------------------------------ discoverability
    @app.get("/robots.txt")
    async def robots():
        # We WANT bots here: that is the product. Everyone is welcome; please come often.
        body = "User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /v1/admin/\nSitemap: https://botspotter.app/sitemap.xml\n\n# Hello, bot. You are being counted — and named. See /claim to claim your name.\n"
        return Response(body, media_type="text/plain", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/sitemap.xml")
    async def sitemap(request: Request):
        feed = await request.app.state.counters.feed(200)
        urls = ["https://botspotter.app/", "https://botspotter.app/about", "https://botspotter.app/sensors",
                "https://botspotter.app/claim", "https://botspotter.app/terms"]
        urls += [f"https://botspotter.app/bot/{f['slug']}" for f in feed if f.get("slug")]
        xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + \
              "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>"
        return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=600"})

    @app.get("/feed.xml")
    async def rss(request: Request):
        """RSS of newly discovered bots — subscribe, embed, or let aggregators pick it up."""
        from datetime import datetime, timezone
        from xml.sax.saxutils import escape as x

        items = await request.app.state.counters.feed(50)
        def rfc822(ts): return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        body = "".join(
            f"<item><title>{x(i['name'])} discovered ({x(i.get('operator') or 'unidentified')})</title>"
            f"<link>https://botspotter.app/bot/{x(i['slug'])}</link><guid>https://botspotter.app/bot/{x(i['slug'])}</guid>"
            f"<pubDate>{rfc822(i['ts'])}</pubDate><description>{x(i['ua_family'])} · first seen in {x(i.get('country',''))} on {i.get('sites',1)} site(s)</description></item>"
            for i in items)
        xml = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>BotSpotter — newly discovered AI bots</title>'
               '<link>https://botspotter.app/</link><description>Every AI bot on the internet, counted and named.</description>'
               f'{body}</channel></rss>')
        return Response(xml, media_type="application/rss+xml", headers={"Cache-Control": "public, max-age=300"})

    @app.get("/avatar/{slug}.svg")
    async def avatar(request: Request, slug: str, size: int = Query(160, ge=24, le=512)):
        """Deterministic robot avatar for a bot (drawn from its fingerprint; no third-party images)."""
        b = await request.app.state.counters.bot_by_slug(slug.lower())
        if b is None:
            raise HTTPException(404, "no bot by that name")
        return Response(avatar_svg(b["fp"], b["name"], size), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400, immutable"})

    @app.get("/badge/{site}.svg")
    async def badge(request: Request, site: str):
        """Live shields-style badge for a sensor site: 'AI bots spotted here · 1,234'."""
        from xml.sax.saxutils import escape as x

        site = site[:40]
        hits = await request.app.state.counters.r.zscore("bs:sensors", site)
        n = f"{int(hits):,}" if hits else "0"
        left, right = "AI bots spotted here", n
        lw, rw = 7 * len(left) + 12, 7 * len(right) + 14
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{lw+rw}" height="20" role="img" aria-label="{x(left)}: {x(right)}">'
               f'<rect width="{lw}" height="20" rx="3" fill="#0e1220"/><rect x="{lw}" width="{rw}" height="20" rx="3" fill="#c6ff4d"/>'
               f'<rect x="{lw-3}" width="6" height="20" fill="#c6ff4d"/>'
               f'<g font-family="Verdana,DejaVu Sans,sans-serif" font-size="11"><text x="6" y="14" fill="#e8ecf5">{x(left)}</text>'
               f'<text x="{lw+7}" y="14" fill="#07090f" font-weight="bold">{x(right)}</text></g></svg>')
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=300"})

    @app.get("/v1/stats/humans")
    async def stats_humans(request: Request, days: int = Query(90, ge=1, le=1100)):
        """Audience report: daily uniques, views, bot hits, countries, sources. Public, cacheable."""
        return cached(await request.app.state.counters.human_report(days), 300)

    @app.get("/v1/stats/humans.csv")
    async def stats_humans_csv(request: Request, days: int = Query(365, ge=1, le=1100)):
        rep = await request.app.state.counters.human_report(days)
        lines = ["date,views,unique_humans,bot_hits,top_countries,top_sources"]
        for r in rep["daily"]:
            cc = " ".join(f"{c}:{n}" for c, n in r["top_countries"]); src = " ".join(f"{c}:{n}" for c, n in r["top_sources"])
            lines.append(f'{r["date"]},{r["views"]},{r["unique_humans"]},{r["bot_hits"]},"{cc}","{src}"')
        return Response("\n".join(lines) + "\n", media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=botspotter-audience.csv", "Cache-Control": "public, max-age=300"})

    # ---- owner-only audience ledger -------------------------------------------------
    def _admin(request: Request, key: str = "") -> None:
        """404 (not 401) when no key is configured or it doesn't match: the route's
        existence is not advertised. Header for the page, ?key= for CSV downloads."""
        want = request.app.state.admin_key
        got = request.headers.get("x-bs-admin") or key
        if not want or not got or not hmac.compare_digest(got, want):
            raise HTTPException(404, "not found")

    async def _visits(request: Request, days: int):
        return await request.app.state.store.visits_since(time.time() - days * 86400)

    @app.get("/v1/admin/visitors")
    async def admin_visitors(request: Request, days: int = Query(90, ge=1, le=1100), key: str = ""):
        _admin(request, key)
        return Response(orjson.dumps(build_ledger(await _visits(request, days), days)), media_type="application/json",
                        headers={"Cache-Control": "private, no-store"})

    @app.get("/v1/admin/visitors.csv")
    async def admin_visitors_csv(request: Request, days: int = Query(1100, ge=1, le=1100), key: str = ""):
        _admin(request, key)
        body = ledger_csv(build_ledger(await _visits(request, days), days))
        return Response(body, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=botspotter-visitors.csv",
                                                              "Cache-Control": "private, no-store"})

    @app.get("/v1/admin/visits.csv")
    async def admin_visits_csv(request: Request, days: int = Query(1100, ge=1, le=1100), key: str = ""):
        _admin(request, key)
        return Response(visits_csv(await _visits(request, days)), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=botspotter-visits.csv", "Cache-Control": "private, no-store"})

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
        @app.get("/stats")
        @app.get("/admin")
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
