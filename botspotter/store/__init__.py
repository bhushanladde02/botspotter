from __future__ import annotations

from ..config import settings
from .base import Store


def make_store(backend: str | None = None) -> Store:
    backend = backend or settings.store_backend
    if backend == "memory":
        from .memory import MemoryStore

        return MemoryStore()
    if backend == "postgres":
        from .postgres import PostgresStore

        return PostgresStore(settings.pg_dsn)
    raise ValueError(f"unknown store backend: {backend}")


__all__ = ["Store", "make_store"]
