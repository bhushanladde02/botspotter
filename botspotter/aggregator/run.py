"""Aggregator: the single consumer that turns the stream into numbers.

Loop:
  poll batch -> Redis counters (+discovery) -> Postgres COPY -> commit
  every snapshot_interval_s -> rewrite bs:snapshot

Exactly one consumer group; Redpanda partitions let us run several
replicas later (each takes a share of partitions). Counters in Redis are
idempotent-ish only at the batch level: a crash between Redis and commit
double-counts that batch once. Acceptable for a fun counter; the Postgres
events table is the source of truth and can rebuild Redis (scripts/rebuild_counters.py).
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time

from ..config import settings
from ..counters import Counters
from ..models import BotEvent, PortalVisit
from ..store import make_store
from ..stream import make_stream

log = logging.getLogger("botspotter.aggregator")


class Aggregator:
    def __init__(self, stream=None, store=None, counters: Counters | None = None):
        self.stream = stream or make_stream()
        self.store = store or make_store()
        self.counters = counters or Counters.from_url()
        self._stop = asyncio.Event()
        self.processed = 0
        self.store_errors = 0

    async def process(self, messages) -> int:
        bots: list[BotEvent] = []
        visits: list[PortalVisit] = []
        for m in messages:
            if m.topic == settings.topic_bot_events:
                bots.append(BotEvent.from_bytes(m.value))
            elif m.topic == settings.topic_portal_visits:
                visits.append(PortalVisit.from_bytes(m.value))
        if bots:
            discovered = await self.counters.record_bot_events(bots)
            await self._store("upsert_bots", self.store.upsert_bots, bots)
            await self._store("write_events", self.store.write_events, bots)
            if discovered:
                await self._store("record_discoveries", self.store.record_discoveries, discovered)
                for d in discovered:
                    log.info("discovered bot %s (%s) on %s sites", d["name"], d["ua_family"], d["sites"])
        if visits:
            await self.counters.record_visits(visits)
            await self._store("write_visits", self.store.write_visits, visits)
        n = len(bots) + len(visits)
        self.processed += n
        return n

    async def _store(self, name: str, fn, payload) -> None:
        """Durable-store writes must never take the live counters down. A failure
        is logged, counted in Redis (bs:errors:<name>) and the batch is still
        committed, so a bad query degrades history instead of crash-looping and
        double-counting (which is exactly what happened on 2026-10-25)."""
        try:
            await fn(payload)
        except Exception:  # noqa: BLE001 - deliberate: isolate the store
            log.exception("store.%s failed for %d items", name, len(payload))
            self.store_errors += 1
            try:
                await self.counters.r.incr(f"bs:errors:{name}")
            except Exception:  # pragma: no cover
                pass

    async def run_once(self, timeout_s: float = 0.5) -> int:
        msgs = await self.stream.poll(
            [settings.topic_bot_events, settings.topic_portal_visits],
            settings.consumer_group, settings.flush_batch_size, timeout_s,
        )
        n = await self.process(msgs)
        if msgs:
            await self.stream.commit()
        return n

    async def run(self) -> None:
        await self.stream.start()
        await self.store.start()
        last_snap = 0.0
        log.info("aggregator started (stream=%s store=%s)", settings.stream_backend, settings.store_backend)
        try:
            while not self._stop.is_set():
                await self.run_once()
                now = time.time()
                if now - last_snap >= settings.snapshot_interval_s:
                    await self.counters.write_snapshot()
                    last_snap = now
        finally:
            await self.counters.write_snapshot()
            await self.store.stop()
            await self.stream.stop()

    def stop(self) -> None:
        self._stop.set()


def main() -> None:  # `botspotter-aggregator`
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    agg = Aggregator()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, agg.stop)
    loop.run_until_complete(agg.run())


if __name__ == "__main__":
    main()
