from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import BotEvent, PortalVisit


class Store(ABC):
    """Durable storage. Redis holds the live numbers; this holds history."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def write_events(self, events: list[BotEvent]) -> None: ...

    @abstractmethod
    async def write_visits(self, visits: list[PortalVisit]) -> None: ...

    @abstractmethod
    async def upsert_bots(self, events: list[BotEvent]) -> None:
        """Insert registry rows for fingerprints not yet known (no-op for known)."""

    @abstractmethod
    async def record_discoveries(self, items: list[dict]) -> None: ...

    async def visits_since(self, since_ts: float, limit: int = 200_000) -> list[PortalVisit]:
        """Raw visit rows newest first, for the owner-only audience ledger. Memory
        store returns what it holds; Postgres reads portal_visits."""
        return []
