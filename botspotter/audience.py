"""Owner-only audience ledger: one row per *individual* visitor, built from the
pseudonymous visit rows (no IP, no cookie — see docs/privacy.rst).

A "visitor" is the stable salted hash of secret+ip+browser. The same person on
the same connection and browser is one row across days; the same person on a
phone and a laptop is two rows. That is the honest limit of counting without an
identifier in the browser, and the ledger says so.

A "session" is a run of page views by one visitor with gaps under 30 minutes —
the same definition Google Analytics uses, so the numbers are comparable.
"""
from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .models import PortalVisit

SESSION_GAP_S = 30 * 60


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _top(counter: Counter, n: int = 3) -> list[list]:
    return [[k, v] for k, v in counter.most_common(n) if k]


def _mode(values) -> str:
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else ""


def build_ledger(visits: list[PortalVisit], days: int) -> dict:
    """visits: newest first. Returns summary + per-visitor rows (most recent first)."""
    by_vs: dict[str, list[PortalVisit]] = defaultdict(list)
    for v in visits:
        by_vs[v.vs or v.visitor].append(v)

    rows = []
    total_sessions = 0
    for key, vs_rows in by_vs.items():
        vs_rows.sort(key=lambda v: v.ts)
        sessions = 1
        for a, b in zip(vs_rows, vs_rows[1:]):
            if b.ts - a.ts > SESSION_GAP_S:
                sessions += 1
        total_sessions += sessions
        first, last = vs_rows[0], vs_rows[-1]
        countries = Counter(v.country for v in vs_rows)
        cities = Counter(f"{v.city}, {v.region}".strip(", ") for v in vs_rows if v.city)
        refs = Counter(v.ref for v in vs_rows if v.ref and v.ref not in ("internal",))
        pages = Counter(v.page for v in vs_rows)
        rows.append({
            "id": key[:12],
            "first_seen": _iso(first.ts), "last_seen": _iso(last.ts),
            "days_active": len({_day(v.ts) for v in vs_rows}),
            "sessions": sessions, "views": len(vs_rows),
            "country": countries.most_common(1)[0][0], "countries": _top(countries),
            "city": cities.most_common(1)[0][0] if cities else "",
            "browser": _mode(v.browser for v in vs_rows), "os": _mode(v.os for v in vs_rows),
            "device": _mode(v.device for v in vs_rows), "lang": _mode(v.lang for v in vs_rows),
            "first_ref": first.ref, "refs": _top(refs), "pages": _top(pages, 5),
            "returning": len({_day(v.ts) for v in vs_rows}) > 1,
        })
    rows.sort(key=lambda r: r["last_seen"], reverse=True)

    daily: dict[str, set] = defaultdict(set)
    daily_views: Counter = Counter()
    for v in visits:
        daily[_day(v.ts)].add(v.vs or v.visitor)
        daily_views[_day(v.ts)] += 1
    summary = {
        "days": days,
        "visitors": len(rows),
        "returning_visitors": sum(1 for r in rows if r["returning"]),
        "sessions": total_sessions,
        "views": len(visits),
        "countries": _top(Counter(v.country for v in visits), 10),
        "cities": _top(Counter(f"{v.city}, {v.region}".strip(", ") for v in visits if v.city), 10),
        "browsers": _top(Counter(v.browser for v in visits), 6),
        "os": _top(Counter(v.os for v in visits), 6),
        "devices": _top(Counter(v.device for v in visits), 3),
        "languages": _top(Counter(v.lang for v in visits), 6),
        "sources": _top(Counter(v.ref for v in visits if v.ref != "internal"), 10),
        "pages": _top(Counter(v.page for v in visits), 10),
        "daily": [{"date": d, "visitors": len(daily[d]), "views": daily_views[d]} for d in sorted(daily)],
        "method": "visitor = salted hash of IP+browser (no IP stored, no cookie); session = views with gaps < 30 min; "
                  "one person on two devices counts twice",
    }
    return {"summary": summary, "visitors": rows, "generated_at": _iso(datetime.now(tz=timezone.utc).timestamp())}


def visits_csv(visits: list[PortalVisit]) -> str:
    """Raw per-visit export (newest first) for a spreadsheet or an evidence binder."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ts_utc", "visitor_id", "country", "region", "city", "page", "referrer", "browser", "os", "device", "lang"])
    for v in visits:
        w.writerow([_iso(v.ts), (v.vs or v.visitor)[:12], v.country, v.region, v.city, v.page, v.ref, v.browser, v.os, v.device, v.lang])
    return out.getvalue()


def ledger_csv(ledger: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["visitor_id", "first_seen_utc", "last_seen_utc", "days_active", "sessions", "views", "country", "city",
                "browser", "os", "device", "lang", "first_referrer", "returning"])
    for r in ledger["visitors"]:
        w.writerow([r["id"], r["first_seen"], r["last_seen"], r["days_active"], r["sessions"], r["views"], r["country"],
                    r["city"], r["browser"], r["os"], r["device"], r["lang"], r["first_ref"], "yes" if r["returning"] else "no"])
    return out.getvalue()
