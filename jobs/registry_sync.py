"""Daily job: refresh botspotter/data/known_bots.json from public registries.

Sources (all free, no auth):
  - ai.robots.txt (GitHub): community list of AI crawler user agents
  - Cloudflare Radar bots directory (optional; needs CF_API_TOKEN) — operator + category

Runs in GitHub Actions (.github/workflows/jobs.yml) and opens a PR when the
registry changes, so every new bot goes through a human eye once. When the
project grows this moves to an Airflow DAG unchanged (docs/growth.md).

Usage:  python jobs/registry_sync.py [--write]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "botspotter" / "data" / "known_bots.json"
AI_ROBOTS_TXT = "https://raw.githubusercontent.com/ai-robots-txt/ai.robots.txt/main/robots.json"
CF_RADAR_BOTS = "https://api.cloudflare.com/client/v4/radar/bots?limit=500&format=json"

CATEGORY_MAP = {  # ai.robots.txt "function" text -> our category
    "ai data scraper": "training", "ai training": "training", "llm training": "training",
    "ai search": "search", "search": "search", "ai assistant": "agent", "ai agent": "agent",
    "agent": "agent", "scraper": "scraper", "seo": "seo",
}


def fetch_json(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "botspotter-registry-sync/0.1", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def guess_category(text: str) -> str:
    t = (text or "").lower()
    for k, v in CATEGORY_MAP.items():
        if k in t:
            return v
    return "other"


def from_ai_robots_txt() -> list[dict]:
    data = fetch_json(AI_ROBOTS_TXT)
    out = []
    for name, meta in data.items():
        out.append({
            "pattern": name, "operator": meta.get("operator", "unknown") or "unknown",
            "category": guess_category(meta.get("function", "")),
            "homepage": "", "source": "ai.robots.txt",
        })
    return out


def from_cloudflare_radar() -> list[dict]:
    token = os.environ.get("CF_API_TOKEN")
    if not token:
        return []
    data = fetch_json(CF_RADAR_BOTS, {"Authorization": f"Bearer {token}"})
    out = []
    for b in data.get("result", {}).get("bots", []):
        cat = (b.get("category") or "").lower()
        if "ai" not in cat and "crawler" not in cat and "search" not in cat:
            continue
        out.append({
            "pattern": b.get("name") or b.get("slug"), "operator": b.get("operator", "unknown") or "unknown",
            "category": guess_category(cat), "homepage": b.get("url", "") or "", "source": "cloudflare-radar",
        })
    return out


def merge(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], list[str]]:
    by_pat = {b["pattern"].lower(): b for b in existing}
    added = []
    for b in incoming:
        key = (b.get("pattern") or "").lower()
        if not key or len(key) < 3:
            continue
        if key in by_pat:
            cur = by_pat[key]
            if cur.get("operator", "unknown") == "unknown" and b.get("operator", "unknown") != "unknown":
                cur["operator"] = b["operator"]
            if not cur.get("homepage") and b.get("homepage"):
                cur["homepage"] = b["homepage"]
            continue
        by_pat[key] = {k: b[k] for k in ("pattern", "operator", "category", "homepage")}
        added.append(b["pattern"])
    return sorted(by_pat.values(), key=lambda x: x["pattern"].lower()), added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write changes to known_bots.json")
    args = ap.parse_args()

    reg = json.loads(REGISTRY.read_text())
    incoming = []
    for src in (from_ai_robots_txt, from_cloudflare_radar):
        try:
            incoming += src()
        except Exception as exc:  # one source down must not block the other
            print(f"warn: {src.__name__} failed: {exc}", file=sys.stderr)
    merged, added = merge(reg["bots"], incoming)
    print(f"registry: {len(reg['bots'])} -> {len(merged)} bots; added {len(added)}: {', '.join(added[:20])}")
    if args.write and added:
        reg["bots"] = merged
        reg["version"] = date.today().isoformat()
        REGISTRY.write_text(json.dumps(reg, indent=2) + "\n")
        print("written", REGISTRY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
