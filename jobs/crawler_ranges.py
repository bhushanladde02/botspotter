#!/usr/bin/env python3
"""Build crawler_ranges.json from the operators' official published IP lists.

    python jobs/crawler_ranges.py /path/to/runtime/crawler_ranges.json

Runs on the VM weekly (deploy/refresh-data.sh) and can be run locally. Each source
is a small JSON with {"prefixes": [{"ipv4Prefix": ...} | {"ipv6Prefix": ...}]}
(Google's format, which the others copied). A source that fails is skipped and
the previous ranges for that operator are kept, so a vendor outage never turns
every one of its bots into an "impostor".
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from botspotter.verify import SOURCES  # noqa: E402


def fetch(url: str) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "BotSpotter range sync (+https://botspotter.app)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    out = []
    for p in data.get("prefixes", []):
        for k in ("ipv4Prefix", "ipv6Prefix"):
            if p.get(k):
                out.append(p[k])
    return out


def main(out_path: str) -> int:
    out = Path(out_path)
    prev = {}
    if out.exists():
        try:
            prev = json.loads(out.read_text()).get("ranges", {})
        except Exception:
            prev = {}
    ranges: dict[str, list[str]] = {}
    for operator, urls in SOURCES.items():
        got: list[str] = []
        ok = True
        for u in urls:
            try:
                got += fetch(u)
            except Exception as e:  # noqa: BLE001
                print(f"  {operator}: {u} failed ({e.__class__.__name__}); keeping previous")
                ok = False
        if got and ok:
            ranges[operator] = sorted(set(got))
        elif prev.get(operator):
            ranges[operator] = prev[operator]
        print(f"  {operator}: {len(ranges.get(operator, []))} prefixes")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"_updated": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"), "ranges": ranges}, indent=0))
    print(f"wrote {out} ({sum(len(v) for v in ranges.values())} prefixes, {len(ranges)} operators)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "botspotter/data/crawler_ranges.json"))
