from __future__ import annotations

import asyncio
from collections import defaultdict

from .base import Message, Stream


class MemoryStream(Stream):
    """In-process queue. Process-shared singleton so ingest and aggregator
    can run in one process for dev/tests. Not durable — by design."""

    _instance: "MemoryStream | None" = None

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[Message]] = defaultdict(asyncio.Queue)
        self.published: int = 0

    @classmethod
    def shared(cls) -> "MemoryStream":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish(self, topic: str, key: bytes, value: bytes) -> None:
        self.published += 1
        await self._queues[topic].put(Message(topic, key, value))

    async def poll(self, topics: list[str], group: str, max_records: int, timeout_s: float) -> list[Message]:
        out: list[Message] = []
        deadline = asyncio.get_event_loop().time() + timeout_s
        while len(out) < max_records:
            got = False
            for t in topics:
                q = self._queues[t]
                try:
                    out.append(q.get_nowait())
                    got = True
                    if len(out) >= max_records:
                        break
                except asyncio.QueueEmpty:
                    continue
            if not got:
                if out or asyncio.get_event_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.01)
        return out

    async def commit(self) -> None:
        return None

    def pending(self, topic: str) -> int:
        return self._queues[topic].qsize()
