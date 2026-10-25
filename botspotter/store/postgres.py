from __future__ import annotations

from datetime import datetime, timezone

import asyncpg

from ..fingerprint import slugify_name
from ..models import BotEvent, PortalVisit
from .base import Store


def _ts(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc)


class PostgresStore(Store):
    """asyncpg with COPY for events — the only way to keep Python fast at
    thousands of inserts/second."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=4)

    async def stop(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def write_events(self, events: list[BotEvent]) -> None:
        if not events or not self.pool:
            return
        rows = [
            (_ts(e.ts), e.site, e.fp, e.kind, e.operator, e.category, e.country, e.region, e.gh, e.hit_robots)
            for e in events
        ]
        async with self.pool.acquire() as con:
            await con.copy_records_to_table(
                "events",
                records=rows,
                columns=["ts", "site", "fp", "kind", "operator", "category", "country", "region", "gh", "hit_robots"],
            )

    async def write_visits(self, visits: list[PortalVisit]) -> None:
        if not visits or not self.pool:
            return
        rows = [(_ts(v.ts), v.visitor, v.country, v.page) for v in visits]
        async with self.pool.acquire() as con:
            await con.copy_records_to_table("portal_visits", records=rows, columns=["ts", "visitor", "country", "page"])

    async def upsert_bots(self, events: list[BotEvent]) -> None:
        if not events or not self.pool:
            return
        seen: dict[str, BotEvent] = {}
        for e in events:
            seen.setdefault(e.fp, e)
        rows = [
            (e.fp, e.fam, e.name, slugify_name(e.name), e.ua_family, e.kind, e.operator, e.category, e.label,
             _ts(e.ts), e.country)
            for e in seen.values()
        ]
        async with self.pool.acquire() as con:
            await con.executemany(
                """INSERT INTO bots (fp, fam, name, slug, ua_family, kind, operator, category, label, first_seen, first_country)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                   ON CONFLICT (fp) DO NOTHING""",
                rows,
            )
            # Lineage: link to the previous sibling in the same family, if any.
            await con.execute(
                """UPDATE bots b SET evolved_from = prev.fp
                   FROM LATERAL (SELECT fp FROM bots p WHERE p.fam = b.fam AND p.first_seen < b.first_seen
                                 ORDER BY p.first_seen DESC LIMIT 1) prev
                   WHERE b.evolved_from IS NULL AND b.fp = ANY($1::text[])""",
                [r[0] for r in rows],
            )

    async def record_discoveries(self, items: list[dict]) -> None:
        if not items or not self.pool:
            return
        async with self.pool.acquire() as con:
            await con.executemany(
                "INSERT INTO discoveries (ts, fp, sites) VALUES ($1, $2, $3)",
                [(_ts(i["ts"]), i["fp"], int(i["sites"])) for i in items],
            )
            await con.executemany(
                "UPDATE bots SET discovered_at = $1 WHERE fp = $2 AND discovered_at IS NULL",
                [(_ts(i["ts"]), i["fp"]) for i in items],
            )
