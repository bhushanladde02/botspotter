"""Live state in Redis: counters, bot registry cache, map buckets,
human-visitor HyperLogLog, discovery feed and the snapshot.

Key layout (all prefixed ``bs:``)::

  bs:total                          total bot hits, all time
  bs:total:d:<YYYYMMDD>             bot hits per UTC day (expires 400d)
  bs:total:h:<YYYYMMDDHH>           bot hits per UTC hour (expires 14d)
  bs:bots                           ZSET fp -> hit count
  bs:bot:<fp>                       HASH name, slug, ua_family, kind, operator, category,
                                    label, first_seen, last_seen, sites(HLL key), fam
  bs:bot:<fp>:h:<YYYYMMDDHH>        per-bot hourly hits (sparkline, expires 14d)
  bs:bot:<fp>:countries             ZSET country -> hits
  bs:bot:<fp>:gh                    ZSET geohash(3) -> hits (where this bot comes from; map focus)
  bs:bot:<fp>:sites                 HLL of site ids (how many sites it crawled)
  bs:bot:<fp>:recent                LIST of last 25 sightings {ts,site,country,gh} (newest first)
  bs:sightings                      LIST of last 100 sightings across all bots (live ticker)
  bs:cat:h:<YYYYMMDDHH>             ZSET category -> hits per hour (category sparkline)
  bs:name:<slug>                    fp (exact-name lookup)
  bs:names                          ZSET slug -> 0 (prefix search via ZRANGEBYLEX)
  bs:fam:<fam>                      ZSET fp -> first_seen (lineage: "evolved from")
  bs:country                        ZSET country -> hits
  bs:cat                            ZSET category -> hits
  bs:op                             ZSET operator -> hits
  bs:gh:<p>                         ZSET geohash(precision p) -> hits   (p in 2,3,4)
  bs:discover:<fp>                  SET of site ids seen in window (expires)
  bs:feed                           LIST of discovered-bot JSON (newest first, capped)
  bs:humans:views                   total portal page views (all time)
  bs:humans:views:d:<YYYYMMDD>      views per day
  bs:humans:hll:d:<YYYYMMDD>        HLL unique humans per day
  bs:humans:hll:w:<YYYYWW>          HLL unique humans per ISO week
  bs:humans:hll:all                 HLL unique humans all time (fed with the stable
                                    hash `vs`, so a returning person counts once)
  bs:humans:hll:h:<YYYYMMDDHH>      HLL unique humans per hour (today's peak; expires 14d)
  bs:humans:country                 ZSET country -> human views
  bs:humans:source                  ZSET referrer source (domain / 'direct') -> human views
  bs:humans:source:d:<YYYYMMDD>     same, per day
  bs:sensors                        ZSET site -> hits
  bs:snapshot                       JSON string, rewritten every second by aggregator
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import orjson
import redis.asyncio as aioredis

from .config import settings
from .fingerprint import slugify_name
from .models import BotEvent, PortalVisit

FEED_MAX = 200
TOP_N = 25
DAILY_TTL = 1100 * 86400  # keep daily human/bot aggregates ~3 years (reach evidence)
PRESENCE_WINDOW_S = 90   # a heartbeat every 30 s; 3 missed = gone
RECENT_MAX = 25
SIGHTINGS_MAX = 100


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")


def _hour(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d%H")


def _week(ts: float) -> str:
    d = datetime.fromtimestamp(ts, tz=timezone.utc).isocalendar()
    return f"{d[0]}{d[1]:02d}"


class Counters:
    def __init__(self, r: aioredis.Redis):
        self.r = r

    @classmethod
    def from_url(cls, url: str | None = None) -> "Counters":
        return cls(aioredis.from_url(url or settings.redis_url, decode_responses=False))

    # ------------------------------------------------------------------ bots
    async def record_bot_events(self, events: list[BotEvent]) -> list[dict]:
        """Apply a batch of bot events. Returns newly discovered bots (for
        the feed and for the Postgres registry insert)."""
        if not events:
            return []
        new_fps: list[str] = []
        # First pass: which fingerprints are brand new? (one round trip)
        fps = list({e.fp for e in events})
        exists = await self.r.pipeline(transaction=False)
        for fp in fps:
            exists.exists(f"bs:bot:{fp}")
        flags = await exists.execute()
        unseen = {fp for fp, f in zip(fps, flags) if not f}

        p = self.r.pipeline(transaction=False)
        for e in events:
            ts = e.ts
            d, h = _day(ts), _hour(ts)
            p.incr("bs:total")
            p.incr(f"bs:total:d:{d}")
            p.expire(f"bs:total:d:{d}", DAILY_TTL)
            p.incr(f"bs:total:h:{h}")
            p.expire(f"bs:total:h:{h}", 14 * 86400)
            p.zincrby("bs:bots", 1, e.fp)
            p.zincrby("bs:country", 1, e.country)
            p.zincrby("bs:cat", 1, e.category)
            p.zincrby("bs:op", 1, e.operator)
            p.zincrby("bs:sensors", 1, e.site)
            for prec in (2, 3, 4):
                p.zincrby(f"bs:gh:{prec}", 1, e.gh[:prec])
            p.incr(f"bs:bot:{e.fp}:h:{h}")
            p.expire(f"bs:bot:{e.fp}:h:{h}", 14 * 86400)
            p.zincrby(f"bs:bot:{e.fp}:countries", 1, e.country)
            p.zincrby(f"bs:bot:{e.fp}:gh", 1, e.gh[:3])
            p.pfadd(f"bs:bot:{e.fp}:sites", e.site)
            p.hset(f"bs:bot:{e.fp}", mapping={"last_seen": ts, "last_country": e.country, "last_site": e.site})
            sighting = orjson.dumps({"ts": ts, "site": e.site, "country": e.country, "gh": e.gh,
                                     "fp": e.fp, "name": e.name, "category": e.category})
            p.lpush(f"bs:bot:{e.fp}:recent", sighting)
            p.ltrim(f"bs:bot:{e.fp}:recent", 0, RECENT_MAX - 1)
            p.lpush("bs:sightings", sighting)
            p.zincrby(f"bs:cat:h:{h}", 1, e.category)
            p.expire(f"bs:cat:h:{h}", 14 * 86400)
            if e.fp in unseen:
                slug = slugify_name(e.name)
                p.hsetnx(f"bs:bot:{e.fp}", "name", e.name)
                p.hsetnx(f"bs:bot:{e.fp}", "slug", slug)
                p.hsetnx(f"bs:bot:{e.fp}", "ua_family", e.ua_family)
                p.hsetnx(f"bs:bot:{e.fp}", "kind", e.kind)
                p.hsetnx(f"bs:bot:{e.fp}", "operator", e.operator)
                p.hsetnx(f"bs:bot:{e.fp}", "category", e.category)
                p.hsetnx(f"bs:bot:{e.fp}", "label", e.label)
                p.hsetnx(f"bs:bot:{e.fp}", "fam", e.fam)
                p.hsetnx(f"bs:bot:{e.fp}", "first_seen", ts)
                p.hsetnx(f"bs:bot:{e.fp}", "first_country", e.country)
                if e.ua_host:
                    p.hsetnx(f"bs:bot:{e.fp}", "ua_host", e.ua_host)
                if e.homepage:
                    p.hsetnx(f"bs:bot:{e.fp}", "registry_homepage", e.homepage)
                p.set(f"bs:name:{slug}", e.fp)
                p.zadd("bs:names", {slug: 0})
                p.zadd(f"bs:fam:{e.fam}", {e.fp: ts}, nx=True)
                if e.fp not in new_fps:
                    new_fps.append(e.fp)
            # Discovery window: distinct sites that saw this fp recently.
            p.sadd(f"bs:discover:{e.fp}", e.site)
            p.expire(f"bs:discover:{e.fp}", settings.discovery_window_s)
        p.ltrim("bs:sightings", 0, SIGHTINGS_MAX - 1)
        await p.execute()

        # Discovery check for anything that is a heuristic (unknown) bot.
        discovered: list[dict] = []
        candidates = {e.fp: e for e in events if e.kind != "known"}
        if candidates:
            p = self.r.pipeline(transaction=False)
            for fp in candidates:
                p.scard(f"bs:discover:{fp}")
                p.hget(f"bs:bot:{fp}", "discovered")
            res = await p.execute()
            p = self.r.pipeline(transaction=False)
            for (fp, e), i in zip(candidates.items(), range(0, len(res), 2)):
                n_sites, already = res[i], res[i + 1]
                if n_sites >= settings.discovery_min_sites and not already:
                    item = {
                        "fp": fp, "name": e.name, "slug": slugify_name(e.name),
                        "ua_family": e.ua_family, "sites": int(n_sites), "ts": e.ts,
                        "country": e.country,
                    }
                    discovered.append(item)
                    p.hset(f"bs:bot:{fp}", "discovered", e.ts)
                    p.lpush("bs:feed", orjson.dumps(item))
            if discovered:
                p.ltrim("bs:feed", 0, FEED_MAX - 1)
                await p.execute()
        # Known bots are "discovered" on first sight — they already have a public identity.
        for fp in new_fps:
            e = next(x for x in events if x.fp == fp)
            if e.kind == "known":
                item = {
                    "fp": fp, "name": e.name, "slug": slugify_name(e.name), "ua_family": e.ua_family,
                    "sites": 1, "ts": e.ts, "country": e.country, "operator": e.operator,
                }
                discovered.append(item)
                await self.r.hset(f"bs:bot:{fp}", "discovered", e.ts)
                await self.r.lpush("bs:feed", orjson.dumps(item))
                await self.r.ltrim("bs:feed", 0, FEED_MAX - 1)
        return discovered

    # ---------------------------------------------------------------- humans
    async def record_visits(self, visits: list[PortalVisit]) -> None:
        if not visits:
            return
        p = self.r.pipeline(transaction=False)
        for v in visits:
            d, w = _day(v.ts), _week(v.ts)
            if v.page == "__heartbeat__":
                # presence only: who is on the page right now
                p.zadd("bs:humans:active", {v.visitor: v.ts})
                continue
            p.zadd("bs:humans:active", {v.visitor: v.ts})
            p.incr("bs:humans:views")
            p.incr(f"bs:humans:views:d:{d}")
            p.expire(f"bs:humans:views:d:{d}", DAILY_TTL)
            p.pfadd(f"bs:humans:hll:d:{d}", v.visitor)
            p.expire(f"bs:humans:hll:d:{d}", DAILY_TTL)
            p.pfadd(f"bs:humans:hll:w:{w}", v.visitor)
            p.expire(f"bs:humans:hll:w:{w}", DAILY_TTL)
            p.zincrby(f"bs:humans:country:d:{d}", 1, v.country)
            p.expire(f"bs:humans:country:d:{d}", DAILY_TTL)
            p.pfadd("bs:humans:hll:all", v.vs or v.visitor)
            p.pfadd(f"bs:humans:hll:h:{_hour(v.ts)}", v.visitor)
            p.expire(f"bs:humans:hll:h:{_hour(v.ts)}", 14 * 86400)
            p.zincrby("bs:humans:country", 1, v.country)
            if v.ref and v.ref != "internal":
                p.zincrby("bs:humans:source", 1, v.ref)
                p.zincrby(f"bs:humans:source:d:{d}", 1, v.ref)
                p.expire(f"bs:humans:source:d:{d}", DAILY_TTL)
        p.zremrangebyscore("bs:humans:active", 0, time.time() - 3600)
        await p.execute()

    # -------------------------------------------------------------- snapshot
    async def build_snapshot(self, ts: float | None = None) -> dict:
        ts = ts or time.time()
        d, h, w = _day(ts), _hour(ts), _week(ts)
        p = self.r.pipeline(transaction=False)
        p.get("bs:total")
        p.get(f"bs:total:d:{d}")
        p.get(f"bs:total:h:{h}")
        p.zcard("bs:bots")
        p.zrevrange("bs:bots", 0, TOP_N - 1, withscores=True)
        p.zrevrange("bs:country", 0, TOP_N - 1, withscores=True)
        p.zrevrange("bs:cat", 0, 10, withscores=True)
        p.zrevrange("bs:op", 0, TOP_N - 1, withscores=True)
        p.zcard("bs:sensors")
        p.zcard("bs:country")
        p.get("bs:humans:views")
        p.get(f"bs:humans:views:d:{d}")
        p.pfcount(f"bs:humans:hll:d:{d}")
        p.pfcount(f"bs:humans:hll:w:{w}")
        p.pfcount("bs:humans:hll:all")
        p.lrange("bs:feed", 0, 9)
        p.llen("bs:feed")
        p.lrange("bs:sightings", 0, 19)
        p.zrevrange("bs:humans:country", 0, 7, withscores=True)
        p.zrevrange("bs:humans:source", 0, 7, withscores=True)
        p.zrevrange(f"bs:humans:source:d:{d}", 0, 7, withscores=True)
        p.zcount("bs:humans:active", ts - PRESENCE_WINDOW_S, "+inf")
        (total, today, hour, n_bots, top, countries, cats, ops, n_sensors, n_countries,
         views, views_today, uniq_today, uniq_week, uniq_all, feed, feed_len, sightings,
         h_countries, h_sources, h_sources_today, watching) = await p.execute()

        # Resolve names for the top bots in one round trip.
        p = self.r.pipeline(transaction=False)
        for fp, _ in top:
            p.hmget(f"bs:bot:{fp.decode()}", "name", "slug", "operator", "category", "kind", "display_name", "claimed_at")
        metas = await p.execute()
        top_bots = []
        for (fp, score), meta in zip(top, metas):
            name, slug, op, cat, kind, dname, claimed = [m.decode() if m else "" for m in meta]
            top_bots.append({"fp": fp.decode(), "name": name, "slug": slug, "operator": op,
                             "category": cat, "kind": kind, "hits": int(score),
                             "display_name": dname or None, "claimed": bool(claimed)})

        # last 24 hourly totals for the sparkline
        hours = []
        base = int(ts // 3600) * 3600
        p = self.r.pipeline(transaction=False)
        for i in range(23, -1, -1):
            p.get(f"bs:total:h:{_hour(base - i * 3600)}")
        hours = [int(x or 0) for x in await p.execute()]

        # today's peaks (UTC day): busiest hour for bot hits and for unique humans
        day_start = int(datetime.fromtimestamp(ts, tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        hours_today = list(range(day_start, int(ts) + 1, 3600))
        p = self.r.pipeline(transaction=False)
        for h0 in hours_today:
            p.get(f"bs:total:h:{_hour(h0)}")
        for h0 in hours_today:
            p.pfcount(f"bs:humans:hll:h:{_hour(h0)}")
        res = await p.execute()
        n = len(hours_today)
        bot_hours = [int(x or 0) for x in res[:n]]
        human_hours = [int(x or 0) for x in res[n:]]
        peak_bots_i = max(range(n), key=lambda i: bot_hours[i]) if n else 0
        peak_humans_i = max(range(n), key=lambda i: human_hours[i]) if n else 0
        peak = {
            "bots": {"hits": bot_hours[peak_bots_i] if n else 0, "hour_utc": datetime.fromtimestamp(hours_today[peak_bots_i], tz=timezone.utc).hour if n else 0},
            "humans": {"unique": human_hours[peak_humans_i] if n else 0, "hour_utc": datetime.fromtimestamp(hours_today[peak_humans_i], tz=timezone.utc).hour if n else 0},
            "humans_now": human_hours[-1] if n else 0,  # unique humans in the current hour
        }

        # category x hour for the last 24h (stacked sparkline)
        p = self.r.pipeline(transaction=False)
        for i in range(23, -1, -1):
            p.zrange(f"bs:cat:h:{_hour(base - i * 3600)}", 0, -1, withscores=True)
        cat_hours_raw = await p.execute()
        cat_names = ["training", "search", "agent", "scraper", "seo", "other", "unknown"]
        cat_hours = {c: [0] * 24 for c in cat_names}
        for i, row in enumerate(cat_hours_raw):
            for c, v in row:
                cat_hours.setdefault(c.decode(), [0] * 24)[i] = int(v)

        return {
            "ts": ts,
            "bots": {
                "total_hits": int(total or 0),
                "hits_today": int(today or 0),
                "hits_this_hour": int(hour or 0),
                "distinct": int(n_bots),
                "discovered": int(feed_len),
                "hourly": hours,
                "top": top_bots,
                "by_country": [{"country": c.decode(), "hits": int(s)} for c, s in countries],
                "by_category": [{"category": c.decode(), "hits": int(s)} for c, s in cats],
                "category_hourly": cat_hours,
                "by_operator": [{"operator": c.decode(), "hits": int(s)} for c, s in ops],
            },
            "humans": {
                "views": int(views or 0),
                "views_today": int(views_today or 0),
                "unique_today": int(uniq_today),
                "unique_week": int(uniq_week),
                "unique_all": int(uniq_all),
                "by_country": [{"country": c.decode(), "views": int(n)} for c, n in h_countries],
                "by_source": [{"source": c.decode(), "views": int(n)} for c, n in h_sources],
                "by_source_today": [{"source": c.decode(), "views": int(n)} for c, n in h_sources_today],
                "watching_now": int(watching),
            },
            "peak": peak,
            "network": {"sensors": int(n_sensors), "countries": int(n_countries)},
            "feed": [orjson.loads(x) for x in feed],
            "sightings": [orjson.loads(x) for x in sightings],
        }

    async def write_snapshot(self) -> dict:
        snap = await self.build_snapshot()
        await self.r.set("bs:snapshot", orjson.dumps(snap))
        return snap

    async def read_snapshot(self) -> dict | None:
        raw = await self.r.get("bs:snapshot")
        return orjson.loads(raw) if raw else None

    # ------------------------------------------------------------------- map
    async def map_buckets(self, precision: int, limit: int = 5000) -> list[dict]:
        precision = max(2, min(4, precision))
        rows = await self.r.zrevrange(f"bs:gh:{precision}", 0, limit - 1, withscores=True)
        from .geohash import decode

        out = []
        for gh, score in rows:
            lat, lon = decode(gh.decode())
            out.append({"gh": gh.decode(), "lat": round(lat, 4), "lon": round(lon, 4), "hits": int(score)})
        return out

    # ------------------------------------------------------------ bot lookup
    async def bot_by_slug(self, slug: str) -> dict | None:
        fp = await self.r.get(f"bs:name:{slug}")
        if not fp:
            return None
        return await self.bot_by_fp(fp.decode())

    async def bot_by_fp(self, fp: str) -> dict | None:
        meta = await self.r.hgetall(f"bs:bot:{fp}")
        if not meta:
            return None
        m = {k.decode(): v.decode() for k, v in meta.items()}
        p = self.r.pipeline(transaction=False)
        p.zscore("bs:bots", fp)
        p.zrevrange(f"bs:bot:{fp}:countries", 0, 9, withscores=True)
        p.pfcount(f"bs:bot:{fp}:sites")
        p.zrange(f"bs:fam:{m.get('fam', '')}", 0, -1, withscores=True)
        p.zrevrange(f"bs:bot:{fp}:gh", 0, 29, withscores=True)
        hits, countries, n_sites, lineage, places_raw = await p.execute()
        from .geohash import decode as _dec
        places = []
        for gh, n in places_raw:
            la, lo = _dec(gh.decode())
            places.append({"lat": round(la, 3), "lon": round(lo, 3), "hits": int(n)})
        base = int(time.time() // 3600) * 3600
        p = self.r.pipeline(transaction=False)
        for i in range(23, -1, -1):
            p.get(f"bs:bot:{fp}:h:{_hour(base - i * 3600)}")
        p.lrange(f"bs:bot:{fp}:recent", 0, RECENT_MAX - 1)
        res = await p.execute()
        hourly = [int(x or 0) for x in res[:24]]
        recent = [orjson.loads(x) for x in res[24]]
        evolved_from = None
        for other, first in lineage:
            o = other.decode()
            if o != fp and float(first) < float(m.get("first_seen", 0)):
                evolved_from = o  # the most recent earlier sibling wins (list is sorted asc)
        ev_meta = None
        if evolved_from:
            ev = await self.r.hmget(f"bs:bot:{evolved_from}", "name", "slug")
            ev_meta = {"fp": evolved_from, "name": ev[0].decode() if ev[0] else "", "slug": ev[1].decode() if ev[1] else ""}
        return {
            "fp": fp,
            "name": m.get("name"), "slug": m.get("slug"), "ua_family": m.get("ua_family"),
            "kind": m.get("kind"), "operator": m.get("operator"), "category": m.get("category"),
            "label": m.get("label"),
            "first_seen": float(m.get("first_seen", 0)), "last_seen": float(m.get("last_seen", 0)),
            "first_country": m.get("first_country"),
            "discovered": float(m["discovered"]) if m.get("discovered") else None,
            "hits": int(hits or 0), "sites": int(n_sites),
            "countries": [{"country": c.decode(), "hits": int(s)} for c, s in countries],
            "places": places,
            "hourly": hourly,
            "last_country": m.get("last_country"), "last_site": m.get("last_site"),
            "recent": [{k: v for k, v in r.items() if k not in ("fp", "name", "category")} for r in recent],
            "evolved_from": ev_meta,
            # claim / identity management (see claims.py)
            "claimed": bool(m.get("claimed_at")),
            "claimed_at": float(m["claimed_at"]) if m.get("claimed_at") else None,
            "claim_method": m.get("claim_method"),
            "display_name": m.get("display_name"),
            "display_slug": m.get("display_slug"),
            "description": m.get("desc"),
            "homepage": m.get("homepage"),
            "contact_url": m.get("contact_url"),
            "ua_host": m.get("ua_host"),
            "registry_homepage": m.get("registry_homepage"),
        }

    async def search(self, q: str, limit: int = 20) -> list[dict]:
        q = slugify_name(q)
        if not q:
            return []
        slugs = await self.r.zrangebylex("bs:names", f"[{q}", f"[{q}\xff", start=0, num=limit)
        if not slugs:
            # substring fallback over the (bounded) name index
            all_slugs = await self.r.zrange("bs:names", 0, 5000)
            slugs = [s for s in all_slugs if q in s.decode()][:limit]
        p = self.r.pipeline(transaction=False)
        for s in slugs:
            p.get(f"bs:name:{s.decode()}")
        fps = await p.execute()
        p = self.r.pipeline(transaction=False)
        seen: set[bytes] = set()
        uniq = [fp for fp in fps if fp and not (fp in seen or seen.add(fp))]
        for fp in uniq:
            p.hmget(f"bs:bot:{fp.decode()}", "name", "slug", "operator", "category", "display_name", "claimed_at")
            p.zscore("bs:bots", fp.decode())
        res = await p.execute()
        out = []
        for i in range(0, len(res), 2):
            name, slug, op, cat, dname, claimed = [x.decode() if x else "" for x in res[i]]
            out.append({"name": name, "slug": slug, "operator": op, "category": cat, "hits": int(res[i + 1] or 0),
                        "display_name": dname or None, "claimed": bool(claimed)})
        return out

    async def human_report(self, days: int = 90) -> dict:
        """Daily audience report (humans + bot hits) for the last N UTC days.
        Built from the daily aggregates, which are kept ~3 years."""
        now = time.time()
        day0 = int(now // 86400) * 86400
        days_list = [day0 - i * 86400 for i in range(days - 1, -1, -1)]
        p = self.r.pipeline(transaction=False)
        for d0 in days_list:
            d = _day(d0)
            p.get(f"bs:humans:views:d:{d}")
            p.pfcount(f"bs:humans:hll:d:{d}")
            p.get(f"bs:total:d:{d}")
            p.zrevrange(f"bs:humans:country:d:{d}", 0, 4, withscores=True)
            p.zrevrange(f"bs:humans:source:d:{d}", 0, 4, withscores=True)
        res = await p.execute()
        rows = []
        for i, d0 in enumerate(days_list):
            v, u, b, cc, src = res[i * 5: i * 5 + 5]
            rows.append({
                "date": datetime.fromtimestamp(d0, tz=timezone.utc).strftime("%Y-%m-%d"),
                "views": int(v or 0), "unique_humans": int(u), "bot_hits": int(b or 0),
                "top_countries": [[c.decode(), int(n)] for c, n in cc],
                "top_sources": [[c.decode(), int(n)] for c, n in src],
            })
        p = self.r.pipeline(transaction=False)
        p.pfcount("bs:humans:hll:all"); p.get("bs:humans:views"); p.zcard("bs:humans:country")
        p.zrevrange("bs:humans:country", 0, 19, withscores=True); p.zrevrange("bs:humans:source", 0, 19, withscores=True)
        uniq_all, views_all, n_countries, countries, sources = await p.execute()
        active_days = [r for r in rows if r["views"]]
        return {
            "generated_at": now, "days": days,
            "totals": {"unique_humans_all_time": int(uniq_all), "views_all_time": int(views_all or 0),
                       "countries_all_time": int(n_countries),
                       "unique_humans_in_window": sum(r["unique_humans"] for r in rows),
                       "views_in_window": sum(r["views"] for r in rows),
                       "peak_day": max(rows, key=lambda r: r["unique_humans"])["date"] if active_days else None,
                       "peak_day_unique_humans": max(r["unique_humans"] for r in rows) if rows else 0},
            "by_country": [{"country": c.decode(), "views": int(n)} for c, n in countries],
            "by_source": [{"source": c.decode(), "views": int(n)} for c, n in sources],
            "daily": rows,
        }

    async def sightings(self, limit: int = 100) -> list[dict]:
        rows = await self.r.lrange("bs:sightings", 0, limit - 1)
        return [orjson.loads(x) for x in rows]

    async def feed(self, limit: int = 50) -> list[dict]:
        rows = await self.r.lrange("bs:feed", 0, limit - 1)
        return [orjson.loads(x) for x in rows]

    async def top_sensors(self, limit: int = 20) -> list[dict]:
        rows = await self.r.zrevrange("bs:sensors", 0, limit - 1, withscores=True)
        return [{"site": s.decode(), "hits": int(h)} for s, h in rows]

    async def lineage(self, fam: str) -> list[dict]:
        rows = await self.r.zrange(f"bs:fam:{fam}", 0, -1, withscores=True)
        p = self.r.pipeline(transaction=False)
        for fp, _ in rows:
            p.hmget(f"bs:bot:{fp.decode()}", "name", "slug")
        metas = await p.execute()
        return [{"fp": fp.decode(), "first_seen": float(fs), "name": m[0].decode() if m[0] else "",
                 "slug": m[1].decode() if m[1] else ""} for (fp, fs), m in zip(rows, metas)]
