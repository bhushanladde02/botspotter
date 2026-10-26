"""End-to-end in one process: ingest API -> memory stream -> aggregator ->
fakeredis counters + memory store -> read API."""
from __future__ import annotations

import time

import httpx
import pytest

from botspotter.aggregator.run import Aggregator
from botspotter.api.app import create_app as create_api
from botspotter.config import settings
from botspotter.ingest.app import create_app as create_ingest
from botspotter.models import SensorHit

from .conftest import (BOT_HEADERS, BROWSER_HEADERS, CHROME_UA, CLAUDE_UA, GPTBOT_UA, LIB_HEADERS, MYSTERY_UA,
                       REQUESTS_UA)


def hits(ua, headers, ip, cc, n=1, path="/"):
    return [{"ua": ua, "headers": headers, "ip": ip, "cf_country": cc, "path": path} for _ in range(n)]


@pytest.fixture
async def world(stream, store, counters, enricher):
    ingest = create_ingest(stream=stream, enricher=enricher)
    api = create_api(counters=counters, stream=stream, enricher=enricher, self_sensor=True)
    agg = Aggregator(stream=stream, store=store, counters=counters)
    async with ingest.router.lifespan_context(ingest), api.router.lifespan_context(api):
        ic = httpx.AsyncClient(transport=httpx.ASGITransport(app=ingest), base_url="http://ingest")
        ac = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://api")
        yield ic, ac, agg, store, counters
        await ic.aclose()
        await ac.aclose()


async def drain(agg):
    total = 0
    while True:
        n = await agg.run_once(timeout_s=0.05)
        if n == 0:
            break
        total += n
    await agg.counters.write_snapshot()
    return total


async def test_auth_required(world):
    ic, *_ = world
    r = await ic.post("/v1/ingest", json={"events": []})
    assert r.status_code == 401
    r = await ic.post("/v1/ingest", json={"events": []}, headers={"X-BS-Key": "devkey"})
    assert r.status_code == 202 and r.json()["site"] == "dev-site"


async def test_bad_batch_rejected(world):
    ic, *_ = world
    r = await ic.post("/v1/ingest", content=b"{not json", headers={"X-BS-Key": "devkey"})
    assert r.status_code == 400


async def test_full_flow_counts_names_and_map(world):
    ic, ac, agg, store, counters = world
    batch = (
        hits(GPTBOT_UA, BOT_HEADERS, "20.171.207.5", "US", n=5)
        + hits(CLAUDE_UA, BOT_HEADERS, "160.79.104.10", "US", n=3, path="/robots.txt")
        + hits(REQUESTS_UA, LIB_HEADERS, "45.33.1.1", "DE", n=2)
        + hits(CHROME_UA, BROWSER_HEADERS, "8.8.8.8", "US", n=4)  # humans: not counted
    )
    r = await ic.post("/v1/ingest", json={"events": batch}, headers={"X-BS-Key": "devkey"})
    assert r.status_code == 202
    assert r.json() == {"accepted": 14, "bots": 10, "site": "dev-site"}

    assert await drain(agg) == 10
    assert len(store.events) == 10 and len(store.bots) == 3
    assert sum(1 for e in store.events if e.hit_robots) == 3
    for e in store.events:  # privacy boundary
        d = e.model_dump()
        assert "ip" not in d and "20.171" not in str(d) and "path" not in d

    snap = (await ac.get("/v1/snapshot")).json()
    assert snap["bots"]["total_hits"] == 10
    assert snap["bots"]["distinct"] == 3
    assert snap["bots"]["hits_today"] == 10
    assert snap["network"]["sensors"] == 1
    assert snap["peak"]["bots"]["hits"] == 10
    top = snap["bots"]["top"]
    assert top[0]["operator"] == "OpenAI" and top[0]["hits"] == 5
    assert all(" #" in b["name"] for b in top)
    assert {c["country"] for c in snap["bots"]["by_country"]} == {"US", "DE"}
    # known bots appear in the discovery feed immediately
    assert {f["operator"] for f in snap["feed"] if "operator" in f} == {"OpenAI", "Anthropic"}

    m = (await ac.get("/v1/map", params={"precision": 2})).json()
    assert sum(b["hits"] for b in m["buckets"]) == 10
    assert len(m["buckets"]) == 2  # US centroid bucket + DE centroid bucket

    # name lookup + search
    slug = top[0]["slug"]
    b = (await ac.get(f"/v1/bots/{slug}")).json()
    assert b["hits"] == 5 and b["operator"] == "OpenAI" and b["sites"] == 1 and b["countries"][0]["country"] == "US"
    assert sum(b["hourly"]) == 5
    s = (await ac.get("/v1/search", params={"q": top[0]["name"].split()[0]})).json()
    assert any(x["slug"] == slug for x in s["results"])
    assert (await ac.get("/v1/bots/nobody-here-0000")).status_code == 404

    # cache headers so Cloudflare can absorb the crowd
    r = await ac.get("/v1/snapshot")
    assert "max-age" in r.headers["cache-control"]


async def test_unknown_bot_discovered_after_three_sites(world, monkeypatch):
    ic, ac, agg, store, counters = world
    ic_app = ic._transport.app  # type: ignore[attr-defined]
    ic_app.state.keys = {"k1": "site-a", "k2": "site-b", "k3": "site-c"}
    for key in ("k1", "k2"):
        await ic.post("/v1/ingest", json={"events": hits(MYSTERY_UA, ["host", "user-agent"], "203.0.113.7", "JP")},
                      headers={"X-BS-Key": key})
    await drain(agg)
    assert store.discoveries == []  # only 2 sites so far
    await ic.post("/v1/ingest", json={"events": hits(MYSTERY_UA, ["host", "user-agent"], "203.0.113.9", "JP")},
                  headers={"X-BS-Key": "k3"})
    await drain(agg)
    assert len(store.discoveries) == 1 and store.discoveries[0]["sites"] == 3
    feed = (await ac.get("/v1/feed")).json()["feed"]
    assert feed[0]["ua_family"] == "weirdnewagent/#"
    b = (await ac.get(f"/v1/bots/{feed[0]['slug']}")).json()
    assert b["discovered"] is not None and b["sites"] == 3
    sensors = (await ac.get("/v1/sensors")).json()["sensors"]
    assert {s["site"] for s in sensors} == {"site-a", "site-b", "site-c"}


async def test_human_visitor_counter(world):
    ic, ac, agg, store, counters = world
    for ip in ("1.1.1.1", "1.1.1.1", "2.2.2.2"):
        r = await ic.post("/v1/beacon", json={"page": "/"},
                          headers={"x-forwarded-for": ip, "user-agent": CHROME_UA, "cf-ipcountry": "IN"})
        assert r.status_code == 204
    await drain(agg)
    snap = (await ac.get("/v1/snapshot")).json()
    h = snap["humans"]
    assert h["views"] == 3 and h["views_today"] == 3
    assert h["unique_today"] == 2 and h["unique_week"] == 2 and h["unique_all"] == 2
    assert len(store.visits) == 3 and all(len(v.visitor) == 24 for v in store.visits)
    assert snap["peak"]["humans"]["unique"] == 2 and snap["peak"]["humans_now"] == 2
    assert 0 <= snap["peak"]["humans"]["hour_utc"] <= 23


async def test_human_sources_and_countries(world):
    ic, ac, agg, store, counters = world
    cases = [("1.1.1.1", "IN", "https://www.linkedin.com/feed/update/123"), ("2.2.2.2", "US", "https://t.co/abc"),
             ("3.3.3.3", "US", ""), ("4.4.4.4", "DE", "https://botspotter.app/about"), ("5.5.5.5", "US", "https://news.ycombinator.com/item?id=1")]
    for ip, cc, ref in cases:
        await ic.post("/v1/beacon", json={"page": "/", "ref": ref},
                      headers={"x-forwarded-for": ip, "user-agent": CHROME_UA, "cf-ipcountry": cc})
    await drain(agg)
    h = (await ac.get("/v1/snapshot")).json()["humans"]
    assert {c["country"]: c["views"] for c in h["by_country"]} == {"US": 3, "IN": 1, "DE": 1}
    src = {c["source"]: c["views"] for c in h["by_source"]}
    assert src == {"linkedin.com": 1, "x.com": 1, "direct": 1, "hacker news": 1}  # internal referrer excluded
    assert all(v.ref and "/" not in v.ref for v in store.visits)  # domains only, never paths


async def test_returning_human_counts_once_all_time(world, monkeypatch):
    """Daily hash rotates (privacy) but the all-time HLL uses the stable hash."""
    import botspotter.enrich as en

    ic, ac, agg, store, counters = world
    real_time = en.time.time
    for day_offset in (0, 1, 2):
        monkeypatch.setattr(en.time, "time", lambda o=day_offset: real_time() + o * 86400)
        await ic.post("/v1/beacon", json={}, headers={"x-forwarded-for": "9.9.9.9", "user-agent": CHROME_UA})
    monkeypatch.setattr(en.time, "time", real_time)
    await drain(agg)
    h = (await ac.get("/v1/snapshot")).json()["humans"]
    assert h["views"] == 3
    assert h["unique_all"] == 1  # same person, three days -> one human
    assert len({v.visitor for v in store.visits}) == 3 and len({v.vs for v in store.visits}) == 1


async def test_portal_is_its_own_sensor(world):
    ic, ac, agg, store, counters = world
    r = await ac.get("/v1/snapshot", headers={"user-agent": GPTBOT_UA, "x-forwarded-for": "20.171.1.2"})
    assert r.status_code == 200
    await drain(agg)
    snap = (await ac.get("/v1/snapshot")).json()
    assert snap["bots"]["total_hits"] >= 1
    assert any(s["site"] == "botspotter-portal" for s in (await ac.get("/v1/sensors")).json()["sensors"])


async def test_lineage_evolved_from(world):
    ic, ac, agg, store, counters = world
    old = hits(GPTBOT_UA, BOT_HEADERS, "20.171.207.5", "US")
    old[0]["ts"] = time.time() - 100
    await ic.post("/v1/ingest", json={"events": old}, headers={"X-BS-Key": "devkey"})
    await drain(agg)
    new = hits(GPTBOT_UA, BOT_HEADERS + ["x-shiny-header"], "20.171.207.5", "US")
    await ic.post("/v1/ingest", json={"events": new}, headers={"X-BS-Key": "devkey"})
    await drain(agg)
    snap = (await ac.get("/v1/snapshot")).json()
    assert snap["bots"]["distinct"] == 2
    newer = min(snap["bots"]["top"], key=lambda b: b["hits"])  # both have 1 hit; pick either and check lineage
    found = False
    for b in snap["bots"]["top"]:
        prof = (await ac.get(f"/v1/bots/{b['slug']}")).json()
        if prof["evolved_from"]:
            found = True
            assert prof["evolved_from"]["name"] and prof["evolved_from"]["fp"] != prof["fp"]
    assert found


def test_enricher_is_fast(enricher):
    hit = SensorHit(ua=GPTBOT_UA, headers=BOT_HEADERS, ip="20.171.207.5", cf_country="US")
    t0 = time.perf_counter()
    n = 20000
    for _ in range(n):
        enricher.bot_event(hit, "bench")
    per = (time.perf_counter() - t0) / n
    assert per < 200e-6, f"{per*1e6:.1f}µs per hit"


def test_parse_batch_formats():
    from botspotter.ingest.app import parse_batch

    one = b'{"ua":"GPTBot/1.0","headers":["host","user-agent"],"ip":"1.2.3.4"}'
    assert len(parse_batch(b'{"events":[' + one + b"]}").events) == 1
    assert len(parse_batch(b"[" + one + b"," + one + b"]").events) == 2
    assert len(parse_batch(one + b"\n" + one + b"\n").events) == 2
    assert parse_batch(one).events[0].ua == "GPTBot/1.0"
    assert parse_batch(b"").events == []


async def test_sightings_and_category_breakdown(world):
    ic, ac, agg, store, counters = world
    batch = (hits(GPTBOT_UA, BOT_HEADERS, "20.171.207.5", "US", n=3)
             + hits("Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)", BOT_HEADERS, "20.171.1.1", "DE", n=2))
    await ic.post("/v1/ingest", json={"events": batch}, headers={"X-BS-Key": "devkey"})
    await drain(agg)
    snap = (await ac.get("/v1/snapshot")).json()
    cats = {c["category"]: c["hits"] for c in snap["bots"]["by_category"]}
    assert cats == {"training": 3, "agent": 2}
    assert sum(snap["bots"]["category_hourly"]["training"]) == 3
    assert sum(snap["bots"]["category_hourly"]["agent"]) == 2
    assert len(snap["sightings"]) == 5 and snap["sightings"][0]["country"] == "DE"
    s = (await ac.get("/v1/sightings", params={"limit": 3})).json()["sightings"]
    assert len(s) == 3 and {"ts", "site", "country", "gh", "fp", "name", "category"} <= set(s[0])
    slug = snap["bots"]["top"][0]["slug"]
    b = (await ac.get(f"/v1/bots/{slug}")).json()
    assert b["last_country"] == "US" and b["last_site"] == "dev-site"
    assert len(b["recent"]) == 3 and set(b["recent"][0]) == {"ts", "site", "country", "gh"}


async def test_store_failure_does_not_crash_or_double_count(world):
    """A broken durable store must not stop counters or replay batches."""
    ic, ac, agg, store, counters = world

    async def boom(_):
        raise RuntimeError("simulated SQL error")

    store.upsert_bots = boom  # type: ignore[assignment]
    await ic.post("/v1/ingest", json={"events": hits(GPTBOT_UA, BOT_HEADERS, "20.171.207.5", "US", n=3)},
                  headers={"X-BS-Key": "devkey"})
    for ip in ("1.1.1.1", "2.2.2.2"):
        await ic.post("/v1/beacon", json={}, headers={"x-forwarded-for": ip, "user-agent": CHROME_UA})
    await drain(agg)
    snap = (await ac.get("/v1/snapshot")).json()
    assert snap["bots"]["total_hits"] == 3          # counted exactly once
    assert snap["humans"]["views"] == 2             # visits still processed after the bot-store failure
    assert agg.store_errors == 1
    assert int(await counters.r.get("bs:errors:upsert_bots")) == 1
    assert len(store.events) == 3                   # the other store writes still happened


async def test_discoverability_endpoints(world):
    ic, ac, agg, store, counters = world
    await ic.post("/v1/ingest", json={"events": hits(GPTBOT_UA, BOT_HEADERS, "20.171.207.5", "US", n=2)}, headers={"X-BS-Key": "devkey"})
    await drain(agg)
    r = await ac.get("/robots.txt"); assert r.status_code == 200 and "Allow: /" in r.text and "Sitemap:" in r.text
    r = await ac.get("/sitemap.xml"); assert r.status_code == 200 and "/claim" in r.text and "/bot/" in r.text
    r = await ac.get("/feed.xml"); assert r.status_code == 200 and "<rss" in r.text and "OpenAI" in r.text
    r = await ac.get("/badge/dev-site.svg"); assert r.status_code == 200 and "<svg" in r.text and ">2<" in r.text
    r = await ac.get("/badge/nobody.svg"); assert r.status_code == 200 and ">0<" in r.text
    r = await ac.get("/static/og.svg"); assert r.status_code == 200


async def test_watching_now_and_audience_report(world):
    ic, ac, agg, store, counters = world
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        await ic.post("/v1/beacon", json={"page": "/", "ref": "https://x.com/a"}, headers={"x-forwarded-for": ip, "user-agent": CHROME_UA, "cf-ipcountry": "US"})
    await ic.post("/v1/beacon", json={"page": "__heartbeat__"}, headers={"x-forwarded-for": "1.1.1.1", "user-agent": CHROME_UA})
    await drain(agg)
    snap = (await ac.get("/v1/snapshot")).json()
    assert snap["humans"]["watching_now"] == 3 and snap["humans"]["views"] == 3  # heartbeat is presence only, not a view
    assert len(store.visits) == 4
    rep = (await ac.get("/v1/stats/humans", params={"days": 7})).json()
    assert len(rep["daily"]) == 7 and rep["daily"][-1]["unique_humans"] == 3 and rep["daily"][-1]["views"] == 3
    assert rep["totals"]["unique_humans_all_time"] == 3 and rep["totals"]["peak_day"] == rep["daily"][-1]["date"]
    assert rep["daily"][-1]["top_countries"] == [["US", 3]] and rep["daily"][-1]["top_sources"] == [["x.com", 3]]
    csv = (await ac.get("/v1/stats/humans.csv", params={"days": 3})).text.splitlines()
    assert csv[0].startswith("date,views,unique_humans") and len(csv) == 4 and csv[-1].split(",")[1:4] == ["3", "3", "0"]
    assert (await ac.get("/stats")).status_code == 200


async def test_bot_profile_has_places(world):
    ic, ac, agg, store, counters = world
    await ic.post("/v1/ingest", json={"events": hits(GPTBOT_UA, BOT_HEADERS, "20.171.207.5", "US", n=3) + hits(GPTBOT_UA, BOT_HEADERS, "20.171.207.6", "DE", n=1)}, headers={"X-BS-Key": "devkey"})
    await drain(agg)
    slug = (await ac.get("/v1/snapshot")).json()["bots"]["top"][0]["slug"]
    b = (await ac.get(f"/v1/bots/{slug}")).json()
    assert len(b["places"]) == 2 and b["places"][0]["hits"] == 3 and {"lat", "lon", "hits"} <= set(b["places"][0])


async def test_snapshot_carries_places_and_centroid_fallback(world):
    """Hover must not need a request: top bots, feed items and ticker sightings
    carry coordinates in the snapshot. Bots that predate per-cell tracking fall
    back to country centroids instead of showing nothing."""
    ic, ac, agg, store, counters = world
    await ic.post("/v1/ingest", json={"events": hits(GPTBOT_UA, BOT_HEADERS, "20.171.207.5", "US", n=2)}, headers={"X-BS-Key": "devkey"})
    await drain(agg)
    s = (await ac.get("/v1/snapshot")).json()
    top = s["bots"]["top"][0]
    assert top["places"] and {"lat", "lon", "hits"} <= set(top["places"][0])
    assert s["feed"][0]["places"] == top["places"]
    assert "lat" in s["sightings"][0] and "lon" in s["sightings"][0]
    # simulate a bot recorded before bs:bot:<fp>:gh existed
    await counters.r.delete(f"bs:bot:{top['fp']}:gh")
    b = (await ac.get(f"/v1/bots/{top['slug']}")).json()
    assert len(b["places"]) == 1 and b["places"][0]["hits"] == 2 and abs(b["places"][0]["lon"] - (-98.5)) < 10
