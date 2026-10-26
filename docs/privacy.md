# Privacy

The product is "name the bots, not the people". These are the rules; the
tests in `tests/test_pipeline.py::test_full_flow_counts_names_and_map` assert
the storage boundary.

## What a sensor sends
Header **names** (not values), User-Agent, client IP, `CF-IPCountry`, method, status, and `path` only when it is `robots.txt`. Sensors drop obvious browsers locally, so most human traffic never leaves the site.

## What ingest keeps
Nothing. The IP is used in-process for (a) the `/16` network prefix of *unknown* bots, (b) GeoIP, (c) the daily human-visitor hash — then discarded. Raw UA is reduced to a version-stripped family. Paths are reduced to one boolean.

## What is stored
`BotEvent` / `PortalVisit` (see `models.py`): timestamps, site id, fingerprint hash, name, UA family, operator/category, country, region, geohash, `hit_robots`. In Postgres and Redis. That is all.

## Humans on the portal
Two salted hashes, both fed only into HyperLogLogs (irreversible, no per-visitor record):
- daily: `blake2b(VISITOR_SECRET + UTC-day + ip + ua)` → "humans today / this week" and today's peak hour; rotates daily so visits can't be linked across days in any stored record;
- stable: `blake2b(VISITOR_SECRET + "all" + ip + ua)` → the "humans all time" HLL only, so a returning person is counted once. It is never written to Postgres or any list; an HLL cannot be enumerated or reversed.
No cookie, no localStorage, no ID handed to the browser.

## Bots are not people — but
A one-person scraper running from a home connection is identified by `UA family + headers + home ISP /16 + country`. That is not personal data under GDPR in any reasonable reading (a /16 is up to 65k addresses), and we never store the address itself. If someone asks for "their" bot to be removed, delete its `bs:bot:<fp>` keys and `bots` row; the name will re-appear on the next hit, so also add the fingerprint to a denylist (TODO when it first happens — see `growth.md`).

## Sensor owners
The site owner installs the sensor knowingly; their key maps to a `site_id` that is shown publicly on the leaderboard. Keys are revocable (remove from `BS_SENSOR_KEYS` / `sensors` table).

## Legal posture
We publish aggregated bot statistics derived from public HTTP requests to consenting sites — the same category of data as Cloudflare Radar's bot reports. No scraping of third parties, no deanonymization, no sale of data.

## Public terms
The user-facing Sensor Terms & Privacy page is `/terms` in `web/index.html` (section `#terms`). Keep it in sync with this file; §5 there mirrors "What is stored" here. The draft was written without a lawyer — get it reviewed before accepting third-party sensors at scale.
