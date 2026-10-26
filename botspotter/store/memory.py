from __future__ import annotations

from ..models import BotEvent, PortalVisit
from .base import Store


class MemoryStore(Store):
    def __init__(self) -> None:
        self.events: list[BotEvent] = []
        self.visits: list[PortalVisit] = []
        self.bots: dict[str, BotEvent] = {}
        self.discoveries: list[dict] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def write_events(self, events: list[BotEvent]) -> None:
        self.events.extend(events)

    async def write_visits(self, visits: list[PortalVisit]) -> None:
        self.visits.extend(visits)

    async def upsert_bots(self, events: list[BotEvent]) -> None:
        for e in events:
            self.bots.setdefault(e.fp, e)

    async def record_discoveries(self, items: list[dict]) -> None:
        self.discoveries.extend(items)

    async def visits_since(self, since_ts: float, limit: int = 200_000) -> list[PortalVisit]:
        rows = [v for v in self.visits if v.ts >= since_ts]
        rows.sort(key=lambda v: v.ts, reverse=True)
        return rows[:limit]
