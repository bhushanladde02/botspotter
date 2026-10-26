"""Wire models.

Sensors send `SensorBatch` (contains the raw IP, used once and dropped).
Ingest turns each item into a `BotEvent` or `PortalVisit` — these are what
go on the stream and into storage, and they contain NO raw IP, no raw UA,
no path, no cookies. That is the privacy boundary of the whole system.
"""
from __future__ import annotations

import time
from typing import Optional

import orjson
from pydantic import BaseModel, Field


class SensorHit(BaseModel):
    ts: Optional[float] = None  # unix seconds; defaults to receive time
    ua: str = ""
    headers: list[str] = Field(default_factory=list)  # header NAMES only, in order
    ip: str = ""
    cf_country: str = ""  # Cloudflare CF-IPCountry if the site is behind CF
    ja4: str = ""
    method: str = "GET"
    status: int = 200
    path: str = ""  # used only for robots.txt detection, never stored


class SensorBatch(BaseModel):
    events: list[SensorHit] = Field(default_factory=list, max_length=1000)


class BotEvent(BaseModel):
    ts: float
    site: str
    fp: str  # fingerprint digest -> the bot's identity
    fam: str  # family digest -> groups renamed/evolved bots
    name: str
    ua_family: str
    kind: str  # known | heuristic
    operator: str
    category: str
    label: str
    country: str
    region: str
    lat: float
    lon: float
    gh: str  # geohash precision 4
    hit_robots: bool = False
    ua_host: str = ""  # public domain the bot names in its UA (+https://x/bot or x@y) — used for claim verification
    homepage: str = ""  # registry homepage (known bots)

    def to_bytes(self) -> bytes:
        return orjson.dumps(self.model_dump())

    @classmethod
    def from_bytes(cls, b: bytes) -> "BotEvent":
        return cls.model_validate(orjson.loads(b))


class PortalVisit(BaseModel):
    ts: float
    visitor: str  # daily-salted hash; rotates every UTC day (today / this week uniques)
    vs: str = ""  # stable salted hash (no date) — fed ONLY into the all-time HyperLogLog, never stored per visit
    country: str
    page: str = "/"

    def to_bytes(self) -> bytes:
        return orjson.dumps(self.model_dump())

    @classmethod
    def from_bytes(cls, b: bytes) -> "PortalVisit":
        return cls.model_validate(orjson.loads(b))


def now() -> float:
    return time.time()
