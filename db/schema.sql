-- BotSpotter schema (PostgreSQL 15+ with TimescaleDB).
-- Applied automatically by the postgres container on first start
-- (docker-compose mounts this file into /docker-entrypoint-initdb.d).

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Registered sensors (sites that send us events). API keys are hashed.
CREATE TABLE IF NOT EXISTS sensors (
    site_id      TEXT PRIMARY KEY,
    key_hash     TEXT NOT NULL,              -- sha256(api_key)
    owner_email  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    active       BOOLEAN NOT NULL DEFAULT true
);

-- The bot registry: one row per fingerprint. Names are deterministic
-- (fingerprint.py) so this is a durable copy of what Redis holds.
CREATE TABLE IF NOT EXISTS bots (
    fp            TEXT PRIMARY KEY,
    fam           TEXT NOT NULL,
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,
    ua_family     TEXT NOT NULL,
    kind          TEXT NOT NULL,             -- known | heuristic
    operator      TEXT NOT NULL,
    category      TEXT NOT NULL,
    label         TEXT NOT NULL,
    first_seen    TIMESTAMPTZ NOT NULL,
    first_country TEXT NOT NULL,
    discovered_at TIMESTAMPTZ,
    evolved_from  TEXT REFERENCES bots(fp)
);
CREATE INDEX IF NOT EXISTS bots_fam_idx ON bots (fam, first_seen);
CREATE INDEX IF NOT EXISTS bots_slug_trgm_idx ON bots (slug text_pattern_ops);

-- Raw (anonymized) events. No IP, no raw UA, no path. 90-day retention.
CREATE TABLE IF NOT EXISTS events (
    ts        TIMESTAMPTZ NOT NULL,
    site      TEXT NOT NULL,
    fp        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    operator  TEXT NOT NULL,
    category  TEXT NOT NULL,
    country   TEXT NOT NULL,
    region    TEXT NOT NULL,
    gh        TEXT NOT NULL,                  -- geohash precision 4
    hit_robots BOOLEAN NOT NULL DEFAULT false
);
SELECT create_hypertable('events', 'ts', if_not_exists => TRUE, chunk_time_interval => INTERVAL '1 day');
SELECT add_retention_policy('events', INTERVAL '90 days', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS events_fp_ts_idx ON events (fp, ts DESC);
CREATE INDEX IF NOT EXISTS events_country_ts_idx ON events (country, ts DESC);

-- Continuous aggregates: hourly rollups kept forever (tiny).
CREATE MATERIALIZED VIEW IF NOT EXISTS events_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', ts) AS bucket, fp, country, category, operator, count(*) AS hits
FROM events GROUP BY bucket, fp, country, category, operator
WITH NO DATA;
SELECT add_continuous_aggregate_policy('events_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes', if_not_exists => TRUE);

CREATE MATERIALIZED VIEW IF NOT EXISTS events_daily_gh
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', ts) AS bucket, gh, count(*) AS hits
FROM events GROUP BY bucket, gh
WITH NO DATA;
SELECT add_continuous_aggregate_policy('events_daily_gh',
    start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE);

-- Portal visits (humans). Visitor hash rotates daily; nothing identifying.
CREATE TABLE IF NOT EXISTS portal_visits (
    ts       TIMESTAMPTZ NOT NULL,
    visitor  TEXT NOT NULL,
    country  TEXT NOT NULL,
    page     TEXT NOT NULL
);
SELECT create_hypertable('portal_visits', 'ts', if_not_exists => TRUE, chunk_time_interval => INTERVAL '1 day');
SELECT add_retention_policy('portal_visits', INTERVAL '90 days', if_not_exists => TRUE);

-- Discovery log (feed history beyond Redis' capped list).
CREATE TABLE IF NOT EXISTS discoveries (
    ts    TIMESTAMPTZ NOT NULL,
    fp    TEXT NOT NULL REFERENCES bots(fp),
    sites INT NOT NULL
);
