"""Sensor site locations for the live map (where an arc lands)."""
from __future__ import annotations

from .runtime import read_json


class SensorSites:
    def __init__(self, data: dict | None = None):
        raw = data if data is not None else read_json("sensors.json", {})
        self.sites: dict[str, dict] = raw.get("sites", {})

    def location(self, site: str) -> dict:
        s = self.sites.get(site)
        if s:
            return {"lat": float(s["lat"]), "lon": float(s["lon"]), "city": s.get("city", "")}
        return {"lat": 0.0, "lon": 0.0, "city": ""}

    def public(self) -> list[dict]:
        return [{"site": k, "city": v.get("city", ""), "lat": v["lat"], "lon": v["lon"], "url": v.get("url", "")}
                for k, v in self.sites.items() if k != "dev-site"]
