from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    topic: str
    key: bytes
    value: bytes


class Stream(ABC):
    """Minimal durable-log interface. Implemented by MemoryStream (tests/dev)
    and KafkaStream (Redpanda in prod). Swapping is config only."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def publish(self, topic: str, key: bytes, value: bytes) -> None: ...

    @abstractmethod
    async def poll(self, topics: list[str], group: str, max_records: int, timeout_s: float) -> list[Message]:
        """Return up to max_records messages, waiting at most timeout_s."""

    @abstractmethod
    async def commit(self) -> None:
        """Acknowledge everything returned by the last poll()."""
