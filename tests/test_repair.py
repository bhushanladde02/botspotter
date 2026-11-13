"""2026-11-12 incident: one batch > 100 events exhausted the Redis pool in the live-map
fan-out *after* counters were applied; the aggregator crashed before commit and the
restart re-applied the batch 2,509 times. These pin the fix, the crash-loop guard and
the targeted repair."""
import asyncio

import fakeredis.aioredis
import httpx
import pytest

from botspotter.aggregator.run import MAX_BATCH_ATTEMPTS, Aggregator
from botspotter.counters import Counters
from botspotter.ingest.app import create_app as create_ingest
from botspotter.repair import apply, plan, trust_from_snapshot
from botspotter.stream.base import Message
from botspotter.stream.memory import MemoryStream

from .conftest import BOT_HEADERS, GPTBOT_UA
from .test_pipeline import drain, hits


@pytest.fixture
async def batch(stream, store, counters, enricher):
    """150 real bot events, counted once, with the store holding them (= Postgres)."""
    ingest = create_ingest(stream=stream, enricher=enricher)
    agg = Aggregator(stream=stream, store=store, counters=counters)
    async with ingest.router.lifespan_context(ingest):
        ic = httpx.AsyncClient(transport=httpx.ASGITransport(app=ingest), base_url="http://ingest")
        for ip, cc in (("20.171.207.5", "US"), ("203.0.113.9", "BE"), ("198.51.100.4", "DE")):
            r = await ic.post("/v1/ingest", json={"events": hits(GPTBOT_UA, BOT_HEADERS, ip, cc, n=50)},
                              headers={"X-BS-Key": "devkey"})
            assert r.status_code == 202
        await ic.aclose()
    await drain(agg)
    return agg, store.events


async def test_live_publish_is_sequential_and_never_fatal(counters, batch):
    agg, events = batch
    inflight = peak = 0

    async def tracked(channel, msg):
        nonlocal inflight, peak
        inflight += 1; peak = max(peak, inflight)
        await asyncio.sleep(0)                        # yield, like a real network call
        inflight -= 1

    counters.r.publish = tracked
    await counters.record_bot_events(events)          # 150 events: old code opened 150 connections at once
    assert peak == 1

    async def broken(channel, msg):
        raise ConnectionError("Too many connections")

    counters.r.publish = broken
    before = int(await counters.r.get("bs:total"))
    await counters.record_bot_events(events)          # must not raise: counts are already applied
    assert int(await counters.r.get("bs:total")) == before + len(events)
    assert int(await counters.r.get("bs:errors:live_publish")) == 1


class ReplayingStream(MemoryStream):
    """Behaves like Kafka after a crash: an uncommitted batch comes back on the next poll."""
    def __init__(self, msgs):
        super().__init__(); self.msgs, self.commits = msgs, 0

    async def poll(self, topics, group, max_records, timeout_s):
        return list(self.msgs)

    async def commit(self):
        self.commits += 1; self.msgs = []


async def test_crash_looping_batch_is_skipped_after_max_attempts(counters, store, batch):
    _, events = batch
    msgs = [Message("bot_events", b"", e.to_bytes()) for e in events[:5]]
    stream = ReplayingStream(msgs)
    agg = Aggregator(stream=stream, store=store, counters=counters)

    async def boom(_events):
        raise RuntimeError("deterministic bug")

    counters.record_bot_events = boom
    for _ in range(MAX_BATCH_ATTEMPTS):               # each "restart" retries the same batch
        with pytest.raises(RuntimeError):
            await agg.run_once()
    assert stream.commits == 0
    assert await agg.run_once() == 0                  # attempt MAX+1: parked and committed, loop moves on
    assert stream.commits == 1
    assert int(await counters.r.get("bs:errors:poison_batches")) == 1
    assert (await counters.r.lrange("bs:agg:poison", 0, -1))[0].startswith(b"bs:agg:attempt:")


async def test_successful_batch_clears_its_attempt_counter(counters, store, batch):
    _, events = batch
    stream = ReplayingStream([Message("bot_events", b"", e.to_bytes()) for e in events[:3]])
    agg = Aggregator(stream=stream, store=store, counters=counters)
    assert await agg.run_once() == 3 and stream.commits == 1
    assert [k async for k in counters.r.scan_iter(match="bs:agg:attempt:*")] == []


async def test_repair_restores_exact_counts_and_leaves_everything_else(counters, batch):
    agg, events = batch
    snap = await counters.write_snapshot()            # last good snapshot (before the replays)
    truth = Counters(fakeredis.aioredis.FakeRedis())  # the same events counted exactly once
    await truth.record_bot_events(events)

    await counters.r.set("bs:sensor_keys:probe", "keep-me")      # stand-ins for state a full Redis wipe would destroy
    await counters.r.hset("bs:site:example.com", "owner", "42")
    names_before = await counters.r.zrange("bs:names", 0, -1)
    views_before = await counters.r.get("bs:humans:views")
    for _ in range(7):                                # the incident: the same batch re-applied
        await counters.record_bot_events(events)
    assert int(await counters.r.get("bs:total")) == 8 * len(events)

    rows = [{"ts": e.ts, "site": e.site, "fp": e.fp, "operator": e.operator, "category": e.category,
             "country": e.country, "gh": e.gh} for e in events]   # what Postgres holds
    await apply(counters.r, plan(rows), trust_from_snapshot(snap))

    assert int(await counters.r.get("bs:total")) == len(events)
    for key in ("bs:bots", "bs:country", "bs:op", "bs:cat", "bs:sensors", "bs:gh:2", "bs:gh:3", "bs:gh:4"):
        assert await counters.r.zrange(key, 0, -1, withscores=True) == await truth.r.zrange(key, 0, -1, withscores=True), key
    fp = events[0].fp
    for key in (f"bs:bot:{fp}:countries", f"bs:bot:{fp}:gh"):
        assert await counters.r.zrange(key, 0, -1, withscores=True) == await truth.r.zrange(key, 0, -1, withscores=True), key
    hour_keys = [k async for k in truth.r.scan_iter(match="bs:total:h:*")]
    for k in hour_keys:
        assert await counters.r.get(k) == await truth.r.get(k)
        assert 0 < await counters.r.ttl(k) <= 14 * 86400
    assert int(await counters.r.get("bs:verified")) == snap["trust"]["verified"]
    assert int(await counters.r.get("bs:impostors")) == snap["trust"]["impostors"]
    # untouched
    assert await counters.r.get("bs:sensor_keys:probe") == b"keep-me"
    assert await counters.r.hget("bs:site:example.com", "owner") == b"42"
    assert await counters.r.zrange("bs:names", 0, -1) == names_before
    assert await counters.r.get("bs:humans:views") == views_before
    assert await counters.r.hget(f"bs:bot:{fp}", "name")          # identity survives


async def test_repair_cli_dry_run_refusal_and_apply(counters, batch, monkeypatch, capsys):
    import botspotter.repair as repair
    from botspotter.counters import Counters as C

    _, events = batch
    await counters.write_snapshot()
    await counters.r.copy("bs:snapshot", "bs:snapshot:good")
    for _ in range(4):
        await counters.record_bot_events(events)
    await counters.write_snapshot()                   # restarted aggregator overwrote bs:snapshot (inflated)
    rows = [{"ts": e.ts, "site": e.site, "fp": e.fp, "operator": e.operator, "category": e.category,
             "country": e.country, "gh": e.gh} for e in events]

    async def fake_rows(_dsn):
        return list(rows)

    monkeypatch.setattr(repair, "_rows_from_postgres", fake_rows)
    monkeypatch.setattr(C, "from_url", classmethod(lambda cls, url=None: counters))

    assert await repair.main([]) == 2                 # inflated snapshot as reference -> refuses
    assert "REFUSING" in capsys.readouterr().out
    assert int(await counters.r.get("bs:total")) == 5 * len(events)

    assert await repair.main(["--snapshot-key", "bs:snapshot:good"]) == 0      # dry run writes nothing
    out = capsys.readouterr().out
    assert "dry run" in out and f"{5 * len(events):,}" in out
    assert int(await counters.r.get("bs:total")) == 5 * len(events)

    assert await repair.main(["--snapshot-key", "bs:snapshot:good", "--apply"]) == 0
    assert int(await counters.r.get("bs:total")) == len(events)
    assert (await counters.read_snapshot())["bots"]["total_hits"] == len(events)
