from __future__ import annotations

from ..config import settings
from .base import Message, Stream


def make_stream(backend: str | None = None) -> Stream:
    backend = backend or settings.stream_backend
    if backend == "memory":
        from .memory import MemoryStream

        return MemoryStream.shared()
    if backend in ("kafka", "redpanda"):
        from .kafka import KafkaStream

        return KafkaStream(settings.kafka_bootstrap)
    raise ValueError(f"unknown stream backend: {backend}")


__all__ = ["Message", "Stream", "make_stream"]
