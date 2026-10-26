# HTTP API

Two services share one hostname. Caddy (production) or Nginx (standalone)
routes `/v1/ingest` and `/v1/beacon` to **ingest** and everything else to
**api**.

## Ingest service

### `POST /v1/ingest`
Header `X-BS-Key: <sensor key>`. Body: `{"events":[SensorHit, …]}`, a bare
JSON array, or newline-delimited JSON. Max 1000 events per request.

`SensorHit`:

| field | type | notes |
|---|---|---|
| `ts` | float | unix seconds; defaults to receive time |
| `ua` | str | User-Agent |
| `headers` | [str] | header **names**, in order |
| `ip` | str | client IP — used once, never stored |
| `cf_country` | str | `CF-IPCountry` if the site is behind Cloudflare |
| `ja4` | str | optional TLS fingerprint |
| `method`, `status` | str, int | |
| `path` | str | only used to detect `robots.txt`; never stored |

Responses: `202 {"accepted": n, "bots": m, "site": "<site_id>"}` ·
`401` invalid key · `400` malformed batch.

### `POST /v1/beacon`
Called by the portal page. Counts a human page view. `204`.

### `GET /healthz` → `{"ok": true, "service": "ingest"}`

## Read API (public, Redis-only, cache headers on everything)

| endpoint | returns | cache |
|---|---|---|
| `GET /v1/snapshot` | counters, top bots, categories, hourly series, feed, sightings, humans, network | 2 s |
| `GET /v1/map?precision=2..4&limit=` | geohash buckets `{gh, lat, lon, hits}` | 5 s |
| `GET /v1/bots/{slug}` | bot profile: name, operator, category, first/last seen, last country/site, hits, sites, countries, hourly, recent sightings, evolved_from | 10 s |
| `GET /v1/search?q=&limit=` | prefix then substring match on names | 10 s |
| `GET /v1/feed?limit=` | newly discovered bots | 5 s |
| `GET /v1/sightings?limit=` | live ticker of recent hits | 2 s |
| `GET /v1/sensors?limit=` | leaderboard of sites by catches | 30 s |
| `GET /` `/about` `/sensors` `/terms` `/bot/{slug}` | the portal (one HTML file, client-routed) | 60 s |
| `GET /healthz` | `{"ok": true, "service": "api"}` | — |

Every request to the API service also passes through the **self-sensor**
middleware: bots that visit the portal are counted under site
`botspotter-portal`.

## Snapshot shape

```text
{
  "ts": 1792984000.1,
  "bots": {"total_hits": 0, "hits_today": 0, "hits_this_hour": 0, "distinct": 0, "discovered": 0,
           "hourly": [24 ints], "top": [...], "by_country": [...], "by_category": [...],
           "by_operator": [...], "category_hourly": {"training": [24 ints], ...}},
  "humans": {"views": 0, "views_today": 0, "unique_today": 0, "unique_week": 0, "unique_all": 0},
  "network": {"sensors": 0, "countries": 0},
  "feed": [...], "sightings": [...]
}
```

## Redis key layout

Documented in the module docstring of {mod}`botspotter.counters`.
