"""Geolocation with a free-tier-friendly fallback chain.

1. MaxMind GeoLite2-City (if BS_GEOIP_DB is set) -> city-level lat/lon + city name
2. Cloudflare's CF-IPCountry header (free on every plan) -> country centroid
3. "XX" / (0, 0)

Plus GeoLite2-ASN (BS_GEOIP_ASN_DB) -> the network the bot lives on ("Microsoft
Azure", "Amazon AWS") — public information about hosting, not about a person.
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
    city: str = ""  # "Ashburn" — only with the City mmdb
    asn: int = 0
    org: str = ""  # ASN organisation as MaxMind names it, e.g. "MICROSOFT-CORP-MSN-AS-BLOCK"
    cloud: str = ""  # friendly hosting label derived from org: "Azure", "AWS", "GCP", "OCI", "Hetzner", ...


# ASN organisation -> the label people recognise. Order matters (first match wins).
_CLOUDS = (
    ("amazon", "AWS"), ("aws", "AWS"), ("microsoft", "Azure"), ("google", "GCP"), ("oracle", "OCI"),
    ("hetzner", "Hetzner"), ("ovh", "OVH"), ("digitalocean", "DigitalOcean"), ("linode", "Akamai/Linode"),
    ("akamai", "Akamai"), ("cloudflare", "Cloudflare"), ("alibaba", "Alibaba Cloud"), ("aliyun", "Alibaba Cloud"),
    ("tencent", "Tencent Cloud"), ("huawei", "Huawei Cloud"), ("vultr", "Vultr"), ("choopa", "Vultr"),
    ("scaleway", "Scaleway"), ("online s.a.s", "Scaleway"), ("contabo", "Contabo"), ("leaseweb", "Leaseweb"),
    ("ionos", "IONOS"), ("fastly", "Fastly"), ("apple", "Apple"), ("meta", "Meta"), ("facebook", "Meta"),
    ("bytedance", "ByteDance"), ("baidu", "Baidu"), ("yandex", "Yandex"), ("comcast", "Comcast (residential)"),
    ("charter", "Spectrum (residential)"), ("verizon", "Verizon"), ("at&t", "AT&T"), ("t-mobile", "T-Mobile"),
    ("deutsche telekom", "Deutsche Telekom"), ("bharti", "Airtel"), ("reliance jio", "Jio"),
)


def cloud_label(org: str) -> str:
    o = (org or "").lower()
    for key, label in _CLOUDS:
        if key in o:
            return label
    return ""


def _open(path: str):
    if not path:
        return None
    try:
        import maxminddb

        return maxminddb.open_database(path)
    except Exception:  # missing DB is a soft failure: we fall back to country centroids
        return None


class Geo:
    def __init__(self, mmdb_path: str = "", asn_path: str = ""):
        self._reader = _open(mmdb_path or settings.geoip_db)
        self._asn = _open(asn_path or settings.geoip_asn_db)

    @property
    def has_mmdb(self) -> bool:
        return self._reader is not None

    def _asn_lookup(self, ip: str) -> tuple[int, str]:
        if self._asn is None or not ip:
            return 0, ""
        try:
            rec = self._asn.get(ip) or {}
        except Exception:
            rec = {}
        return int(rec.get("autonomous_system_number") or 0), str(rec.get("autonomous_system_organization") or "")

    def lookup(self, ip: str, cf_country: str = "") -> GeoResult:
        asn, org = self._asn_lookup(ip)
        cloud = cloud_label(org)
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
                city = ((rec.get("city") or {}).get("names") or {}).get("en", "")
                if lat is not None and lon is not None:
                    return GeoResult(country, region, float(lat), float(lon), "mmdb", city, asn, org, cloud)
        cc = (cf_country or "").upper()
        if cc and cc in _CENTROIDS:
            lat, lon = _CENTROIDS[cc]
            return GeoResult(cc, "", lat, lon, "cf-header", "", asn, org, cloud)
        return GeoResult("XX", "", 0.0, 0.0, "none", "", asn, org, cloud)


def centroid(country: str) -> tuple[float, float]:
    lat, lon = _CENTROIDS.get(country.upper(), _CENTROIDS["XX"])
    return lat, lon
