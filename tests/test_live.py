"""Live map plumbing: impostor verification, hosting labels, the SSE stream."""
import asyncio

import orjson
import pytest

from botspotter.enrich import Enricher
from botspotter.geo import cloud_label
from botspotter.models import SensorHit
from botspotter.verify import Verifier

from .conftest import BOT_HEADERS, GPTBOT_UA
from .test_pipeline import drain, world  # noqa: F401

RANGES = {"ranges": {"OpenAI": ["20.15.240.0/22", "2a02:1234::/32"], "Google": ["66.249.64.0/19"]}}


def test_verifier_verdicts():
    v = Verifier(RANGES)
    assert v.verdict("OpenAI", "20.15.241.9") == "verified"
    assert v.verdict("OpenAI", "198.51.100.7") == "impostor"
    assert v.verdict("OpenAI", "2a02:1234:5::1") == "verified"
    assert v.verdict("Anthropic", "198.51.100.7") == ""          # no published ranges -> unknown, never impostor
    assert v.verdict("OpenAI", "not-an-ip") == ""
    assert Verifier({"ranges": {}}).verdict("OpenAI", "1.2.3.4") == ""   # empty packaged file -> unknown


def test_cloud_labels():
    assert cloud_label("MICROSOFT-CORP-MSN-AS-BLOCK") == "Azure"
    assert cloud_label("AMAZON-02") == "AWS"
    assert cloud_label("Oracle Corporation") == "OCI"
    assert cloud_label("Some ISP") == ""


def test_enricher_marks_impostor_without_keeping_ip():
    e = Enricher(verifier=Verifier(RANGES))
    ev = e.bot_event(SensorHit(ts=1.0, ip="198.51.100.7", ua=GPTBOT_UA, headers=BOT_HEADERS, path="/", cf_country="US"), "dev-site")
    assert ev.verified == "impostor" and "198.51.100.7" not in ev.model_dump_json()
    ev = e.bot_event(SensorHit(ts=1.0, ip="20.15.241.9", ua=GPTBOT_UA, headers=BOT_HEADERS, path="/", cf_country="US"), "dev-site")
    assert ev.verified == "verified"


async def test_live_stream_and_trust_counters(world):
    """httpx's ASGI transport buffers whole responses, so the SSE route itself can't be
    streamed in-process; we assert the pub/sub contract it relays (message shape) and
    that the route answers with the right headers."""
    ic, ac, agg, store, counters = world
    from botspotter.counters import LIVE_CHANNEL

    ps = counters.r.pubsub()
    await ps.subscribe(LIVE_CHANNEL)
    await ic.post("/v1/ingest", json={"events": [
        {"ts": 1.0, "ip": "198.51.100.7", "ua": GPTBOT_UA, "headers": BOT_HEADERS, "path": "/", "cf_country": "US"},
        {"ts": 2.0, "ip": "198.51.100.8", "ua": GPTBOT_UA, "headers": BOT_HEADERS, "path": "/robots.txt", "cf_country": "DE"},
    ]}, headers={"X-BS-Key": "devkey"})
    await drain(agg)
    got = []
    for _ in range(10):   # the first poll may return None while it swallows the subscribe ack
        m = await ps.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if m is not None:
            got.append(orjson.loads(m["data"]))
    assert len(got) == 2
    m = got[0]
    assert {"ts", "name", "slug", "operator", "category", "verified", "lat", "lon", "site", "dlat", "dlon", "dcity"} <= set(m)
    assert m["site"] == "dev-site" and m["dcity"] == "Folsom, CA" and "ip" not in m
    snap = (await ac.get("/v1/snapshot")).json()
    assert "trust" in snap and "by_cloud" in snap and snap["network"]["sites"]
    assert "dev-site" not in {x["site"] for x in snap["network"]["sites"]}   # dev entry is hidden publicly
    assert (await ac.get("/v1/sites")).json()["sites"]
    assert "/v1/live" in {r.path for r in ac._transport.app.routes}
