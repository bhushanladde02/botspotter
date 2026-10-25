"""Geolocation with a free-tier-friendly fallback chain.

1. MaxMind GeoLite2-City (if BS_GEOIP_DB is set) -> city-level lat/lon
2. Cloudflare's CF-IPCountry header (free on every plan) -> country centroid
3. "XX" / (0, 0)

The IP is only ever used in-process; nothing here persists it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from .config import settings

_CENTROIDS: dict[str, list[float]] = json.loads(
    resources.files("botspotter.data").joinpath("country_centroids.json").read_text()
)


@dataclass(frozen=True)
class GeoResult:
    country: str  # ISO-3166 alpha-2 or "XX"
    region: str  # "US-VA", or "" if unknown
    lat: float
    lon: float
    source: str  # "mmdb" | "cf-header" | "none"


class Geo:
    def __init__(self, mmdb_path: str = ""):
        self._reader = None
        path = mmdb_path or settings.geoip_db
        if path:
            try:
                import maxminddb

                self._reader = maxminddb.open_database(path)
            except Exception:  # pragma: no cover - missing DB is a soft failure
                self._reader = None

    @property
    def has_mmdb(self) -> bool:
        return self._reader is not None

    def lookup(self, ip: str, cf_country: str = "") -> GeoResult:
        if self._reader is not None and ip:
            try:
                rec = self._reader.get(ip) or {}
            except Exception:
                rec = {}
            if rec:
                country = (rec.get("country") or {}).get("iso_code") or "XX"
                subs = rec.get("subdivisions") or []
                region = f"{country}-{subs[0]['iso_code']}" if subs and subs[0].get("iso_code") else ""
                loc = rec.get("location") or {}
                lat, lon = loc.get("latitude"), loc.get("longitude")
                if lat is not None and lon is not None:
                    return GeoResult(country, region, float(lat), float(lon), "mmdb")
        cc = (cf_country or "").upper()
        if cc and cc in _CENTROIDS:
            lat, lon = _CENTROIDS[cc]
            return GeoResult(cc, "", lat, lon, "cf-header")
        return GeoResult("XX", "", 0.0, 0.0, "none")


def centroid(country: str) -> tuple[float, float]:
    lat, lon = _CENTROIDS.get(country.upper(), _CENTROIDS["XX"])
    return lat, lon
