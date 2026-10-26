-- Idempotent migrations, applied by PostgresStore.start() on every boot.
-- schema.sql only runs when the data volume is created, so anything added
-- after the first deploy must also appear here (docs/runbook.rst "Schema changes").
-- Lives inside the package (not db/) so the installed wheel carries it.

-- 2026-10-26 audience ledger: pseudonymous per-visit detail (no IP) and a longer retention.
ALTER TABLE portal_visits ADD COLUMN IF NOT EXISTS vs      TEXT NOT NULL DEFAULT '';
ALTER TABLE portal_visits ADD COLUMN IF NOT EXISTS ref     TEXT NOT NULL DEFAULT 'direct';
ALTER TABLE portal_visits ADD COLUMN IF NOT EXISTS city    TEXT NOT NULL DEFAULT '';
ALTER TABLE portal_visits ADD COLUMN IF NOT EXISTS region  TEXT NOT NULL DEFAULT '';
ALTER TABLE portal_visits ADD COLUMN IF NOT EXISTS browser TEXT NOT NULL DEFAULT '';
ALTER TABLE portal_visits ADD COLUMN IF NOT EXISTS os      TEXT NOT NULL DEFAULT '';
ALTER TABLE portal_visits ADD COLUMN IF NOT EXISTS device  TEXT NOT NULL DEFAULT '';
ALTER TABLE portal_visits ADD COLUMN IF NOT EXISTS lang    TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS portal_visits_vs_ts ON portal_visits (vs, ts DESC);
SELECT remove_retention_policy('portal_visits', if_exists => TRUE);
SELECT add_retention_policy('portal_visits', INTERVAL '1100 days', if_not_exists => TRUE);
