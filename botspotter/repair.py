"""Repair inflated bot counters in Redis from Postgres — without touching anything else.

Why this exists: on 2026-11-12 the aggregator crash-looped on one batch for 9.7 h and
re-applied it to Redis 2,509 times (bs:total 12k -> 300k). Postgres was clean (the crash
came before the store write). scripts/rebuild_counters.py rebuilds from Postgres too, but
only after wiping Redis completely — which would also delete sensor keys, enrollments, sessions, claims,
names and human counts. This recounts only the keys a bot event increments:

  rebuilt exactly from Postgres `events`:
    bs:total, bs:total:d:*, bs:total:h:* (14 d), bs:bots, bs:country, bs:cat, bs:op,
    bs:sensors, bs:gh:{2,3,4}, bs:cat:h:* (14 d), bs:bot:<fp>:h:* (14 d),
    bs:bot:<fp>:countries, bs:bot:<fp>:gh
  restored from a snapshot (Postgres does not store these):
    bs:verified, bs:impostors, bs:impostors:op, bs:cloud

Untouched: bot identities (bs:bot:<fp> hashes, names, slugs, claims), recent sightings,
feed, enrollments, keys, humans, check stats.

Run with the aggregator STOPPED (so nothing increments mid-rewrite):

    docker compose stop aggregator
    docker compose run --rm --no-deps aggregator python -m botspotter.repair            # dry run
    docker compose run --rm --no-deps aggregator python -m botspotter.repair --apply    # write
    docker compose start aggregator

Trust/cloud and the safety check use the snapshot in --snapshot-key (default bs:snapshot).
After an incident, save the last GOOD snapshot before restarting the aggregator (which
overwrites bs:snapshot with inflated numbers), and point the repair at the copy:

    docker compose exec redis redis-cli COPY bs:snapshot bs:snapshot:good
    ... python -m botspotter.repair --snapshot-key bs:snapshot:good
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable

import orjson

from .counters import DAILY_TTL, _day, _hour

HOURLY_TTL = 14 * 86400

# Key families a bot event increments (see Counters.record_bot_events).
FIXED_KEYS = ["bs:total", "bs:bots", "bs:country", "bs:cat", "bs:op", "bs:sensors",
              "bs:gh:2", "bs:gh:3", "bs:gh:4"]
PATTERNS = ["bs:total:d:*", "bs:total:h:*", "bs:cat:h:*", "bs:bot:*:h:*", "bs:bot:*:countries", "bs:bot:*:gh"]
TRUST_KEYS = ["bs:verified", "bs:impostors", "bs:impostors:op", "bs:cloud"]


def plan(rows: Iterable[dict], now: float | None = None) -> dict:
    """Compute every bot-counter value from event rows (ts, site, fp, operator, category,
    country, gh). Pure: no Redis. Returns {"strings": {key: int}, "zsets": {key: {member: n}},
    "ttl": {key: seconds}}."""
    now = now or time.time()
    total = 0
    daily: Counter = Counter()
    hourly: Counter = Counter()
    z: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        ts = float(r["ts"])
        total += 1
        daily[f"bs:total:d:{_day(ts)}"] += 1
        recent = now - ts < HOURLY_TTL
        h = _hour(ts)
        fp, gh = r["fp"], r["gh"] or ""
        if recent:
            hourly[f"bs:total:h:{h}"] += 1
            hourly[f"bs:bot:{fp}:h:{h}"] += 1
            z[f"bs:cat:h:{h}"][r["category"]] += 1
        z["bs:bots"][fp] += 1
        z["bs:country"][r["country"]] += 1
        z["bs:cat"][r["category"]] += 1
        z["bs:op"][r["operator"]] += 1
        z["bs:sensors"][r["site"]] += 1
        for prec in (2, 3, 4):
            z[f"bs:gh:{prec}"][gh[:prec]] += 1
        z[f"bs:bot:{fp}:countries"][r["country"]] += 1
        z[f"bs:bot:{fp}:gh"][gh[:3]] += 1
    strings = {"bs:total": total, **daily, **hourly}
    ttl = {k: DAILY_TTL for k in daily} | {k: HOURLY_TTL for k in hourly}
    ttl |= {k: HOURLY_TTL for k in z if k.startswith("bs:cat:h:")}
    return {"strings": strings, "zsets": {k: dict(v) for k, v in z.items()}, "ttl": ttl}


def trust_from_snapshot(snap: dict) -> dict:
    t = snap.get("trust") or {}
    return {
        "strings": {"bs:verified": int(t.get("verified") or 0), "bs:impostors": int(t.get("impostors") or 0)},
        "zsets": {
            "bs:impostors:op": {x["operator"]: int(x["hits"]) for x in t.get("impostors_by_operator") or []},
            "bs:cloud": {x["cloud"]: int(x["hits"]) for x in snap.get("by_cloud") or []},
        },
    }


async def _existing_keys(r) -> list[bytes]:
    keys = []
    for pat in PATTERNS:
        async for k in r.scan_iter(match=pat, count=1000):
            keys.append(k)
    return keys


async def current_summary(r) -> dict:
    p = r.pipeline(transaction=False)
    p.get("bs:total"); p.zcard("bs:bots")
    p.zrevrange("bs:sensors", 0, 9, withscores=True)
    p.get("bs:verified"); p.get("bs:impostors")
    total, n_bots, sensors, ver, imp = await p.execute()
    return {"total": int(total or 0), "bots": int(n_bots or 0),
            "sensors": {s.decode(): int(n) for s, n in sensors},
            "verified": int(ver or 0), "impostors": int(imp or 0)}


async def apply(r, computed: dict, trust: dict | None) -> int:
    """Delete the bot-counter keys and write the recomputed values. Returns keys written."""
    stale = await _existing_keys(r)
    p = r.pipeline(transaction=True)
    for k in FIXED_KEYS + stale + (TRUST_KEYS if trust else []):
        p.delete(k)
    n = 0
    for k, v in computed["strings"].items():
        p.set(k, v, ex=computed["ttl"].get(k)); n += 1
    for k, members in computed["zsets"].items():
        if members:
            p.zadd(k, members)
            if k in computed["ttl"]:
                p.expire(k, computed["ttl"][k])
            n += 1
    if trust:
        for k, v in trust["strings"].items():
            p.set(k, v); n += 1
        for k, members in trust["zsets"].items():
            if members:
                p.zadd(k, members); n += 1
    await p.execute()
    return n


async def _rows_from_postgres(dsn: str) -> list[dict]:
    import asyncpg

    con = await asyncpg.connect(dsn)
    try:
        recs = await con.fetch("SELECT ts, site, fp, operator, category, country, gh FROM events")
    finally:
        await con.close()
    return [{"ts": x["ts"].timestamp(), "site": x["site"], "fp": x["fp"], "operator": x["operator"],
             "category": x["category"], "country": x["country"], "gh": x["gh"]} for x in recs]


async def main(argv: list[str] | None = None) -> int:
    from .config import settings
    from .counters import Counters

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write the repaired values (default: dry run)")
    ap.add_argument("--snapshot-key", default="bs:snapshot",
                    help="Redis key holding the last good snapshot (trust/cloud values + safety check)")
    ap.add_argument("--force", action="store_true", help="apply even if Postgres has fewer events than the snapshot total")
    a = ap.parse_args(argv)

    c = Counters.from_url()
    r = c.r
    raw = await r.get(a.snapshot_key)
    snap = orjson.loads(raw) if raw else None
    if snap is None:
        print(f"no snapshot at {a.snapshot_key}; trust/cloud counters will be left as they are")
    rows = await _rows_from_postgres(settings.pg_dsn)
    computed = plan(rows)
    trust = trust_from_snapshot(snap) if snap else None
    before = await current_summary(r)
    after_sensors = dict(sorted(computed["zsets"].get("bs:sensors", {}).items(), key=lambda x: -x[1])[:10])
    snap_total = int(((snap or {}).get("bots") or {}).get("total_hits") or 0)
    snap_ts = (snap or {}).get("ts")
    print(f"snapshot used for trust: ts={snap_ts} total_hits={snap_total}")
    print(f"{'':12}{'now (Redis)':>16}{'repaired':>16}")
    print(f"{'total':12}{before['total']:>16,}{computed['strings']['bs:total']:>16,}")
    print(f"{'bots':12}{before['bots']:>16,}{len(computed['zsets'].get('bs:bots', {})):>16,}")
    print(f"{'verified':12}{before['verified']:>16,}{(trust or {}).get('strings', {}).get('bs:verified', before['verified']):>16,}")
    print(f"{'impostors':12}{before['impostors']:>16,}{(trust or {}).get('strings', {}).get('bs:impostors', before['impostors']):>16,}")
    for s in sorted(set(before["sensors"]) | set(after_sensors)):
        print(f"{'  ' + s:12}{before['sensors'].get(s, 0):>16,}{after_sensors.get(s, 0):>16,}")
    errs = {k.decode(): int(v) for k, v in [(k, await r.get(k)) async for k in r.scan_iter(match="bs:errors:*")] if v}
    if errs:
        print("recorded errors:", errs)
    if snap_total and computed["strings"]["bs:total"] < snap_total * 0.98 and not a.force:
        print(f"REFUSING: Postgres has {computed['strings']['bs:total']:,} events but the last good snapshot "
              f"counted {snap_total:,}. Postgres may be missing history; check bs:errors:write_events. "
              f"Re-run with --force to apply anyway.")
        return 2
    if not a.apply:
        print("dry run: nothing written. Re-run with --apply to write.")
        return 0
    n = await apply(r, computed, trust)
    await c.write_snapshot()
    print(f"applied: {n} keys written, snapshot rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
