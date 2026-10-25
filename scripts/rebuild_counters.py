"""Rebuild every Redis counter from Postgres (the source of truth).

Use after a Redis loss or a counting bug. Safe to run while live: it writes
into a scratch key prefix is NOT implemented in v1 — stop the aggregator,
FLUSHDB, run this, start the aggregator. Takes ~1 min per 10M events.

    docker compose stop aggregator
    docker compose exec redis redis-cli FLUSHDB
    docker compose run --rm aggregator python -m scripts.rebuild_counters
    docker compose start aggregator
"""
from __future__ import annotations

import asyncio

import asyncpg

from botspotter.config import settings
from botspotter.counters import Counters
from botspotter.models import BotEvent, PortalVisit


async def main() -> None:
    con = await asyncpg.connect(settings.pg_dsn)
    c = Counters.from_url()
    bots = {r["fp"]: r for r in await con.fetch("SELECT * FROM bots")}
    n = 0
    async with con.transaction():
        async for r in con.cursor("SELECT * FROM events ORDER BY ts"):
            b = bots.get(r["fp"])
            if not b:
                continue
            ev = BotEvent(ts=r["ts"].timestamp(), site=r["site"], fp=r["fp"], fam=b["fam"], name=b["name"],
                          ua_family=b["ua_family"], kind=r["kind"], operator=r["operator"], category=r["category"],
                          label=b["label"], country=r["country"], region=r["region"], lat=0, lon=0, gh=r["gh"],
                          hit_robots=r["hit_robots"])
            await c.record_bot_events([ev])
            n += 1
            if n % 10000 == 0:
                print("events", n)
    async with con.transaction():
        async for r in con.cursor("SELECT * FROM portal_visits ORDER BY ts"):
            await c.record_visits([PortalVisit(ts=r["ts"].timestamp(), visitor=r["visitor"], country=r["country"], page=r["page"])])
    await c.write_snapshot()
    print("rebuilt", n, "events")


if __name__ == "__main__":
    asyncio.run(main())
