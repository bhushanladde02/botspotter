"""Runtime configuration, read once from environment variables.

Every setting has a safe local default so `pytest` and `docker compose up`
work with no .env file. See docs/runbook.md for the production values.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # Which backends to use. "memory" is for tests and single-process dev.
    stream_backend: str = field(default_factory=lambda: _env("BS_STREAM", "memory"))
    store_backend: str = field(default_factory=lambda: _env("BS_STORE", "memory"))
    redis_url: str = field(default_factory=lambda: _env("BS_REDIS_URL", "redis://localhost:6379/0"))

    # Redpanda / Kafka
    kafka_bootstrap: str = field(default_factory=lambda: _env("BS_KAFKA_BOOTSTRAP", "localhost:9092"))
    topic_bot_events: str = field(default_factory=lambda: _env("BS_TOPIC_BOT_EVENTS", "bot_events"))
    topic_portal_visits: str = field(default_factory=lambda: _env("BS_TOPIC_PORTAL_VISITS", "portal_visits"))
    consumer_group: str = field(default_factory=lambda: _env("BS_CONSUMER_GROUP", "aggregator"))

    # Postgres
    pg_dsn: str = field(
        default_factory=lambda: _env("BS_PG_DSN", "postgresql://botspotter:botspotter@localhost:5432/botspotter")
    )

    # Ingest auth. Comma-separated "key:site_id" pairs for v1.
    # Production: move to the sensors table (see docs/runbook.md).
    sensor_keys: list[str] = field(default_factory=lambda: _env_list("BS_SENSOR_KEYS", "devkey:dev-site"))

    # GeoIP: optional MaxMind GeoLite2-City.mmdb. Without it we fall back to
    # Cloudflare's CF-IPCountry header + country centroids (still free).
    geoip_db: str = field(default_factory=lambda: _env("BS_GEOIP_DB", ""))

    # Naming salt: changing it renames every bot. Never rotate casually.
    naming_salt: str = field(default_factory=lambda: _env("BS_NAMING_SALT", "botspotter-v1"))

    # Daily salt for human-visitor uniqueness (HyperLogLog). Rotates in code
    # by UTC date; this is just a secret prefix so hashes can't be precomputed.
    visitor_secret: str = field(default_factory=lambda: _env("BS_VISITOR_SECRET", "change-me-in-prod"))

    # Owner key for the audience ledger (/admin, /v1/admin/*). Unset = those routes 404.
    admin_key: str = field(default_factory=lambda: _env("BS_ADMIN_KEY", ""))

    # New-bot discovery: unseen fingerprint on N distinct sites within window.
    discovery_min_sites: int = field(default_factory=lambda: _env_int("BS_DISCOVERY_MIN_SITES", 3))
    discovery_window_s: int = field(default_factory=lambda: _env_int("BS_DISCOVERY_WINDOW_S", 86400))

    # Aggregator cadence
    snapshot_interval_s: float = field(default_factory=lambda: float(_env("BS_SNAPSHOT_INTERVAL_S", "1.0")))
    flush_batch_size: int = field(default_factory=lambda: _env_int("BS_FLUSH_BATCH", 500))

    # Public read API cache TTL (seconds) — Cloudflare honours Cache-Control.
    api_cache_ttl: int = field(default_factory=lambda: _env_int("BS_API_CACHE_TTL", 2))

    def sensor_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for pair in self.sensor_keys:
            if ":" in pair:
                k, site = pair.split(":", 1)
                out[k] = site
        return out


settings = Settings()
