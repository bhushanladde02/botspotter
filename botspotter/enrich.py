"""Turn a raw sensor hit into an anonymized BotEvent (or None if human).

This is the hot path. It must stay allocation-light and must not touch
the network: classification is regex over a short list, geo is an
in-process mmdb lookup or a dict, hashing is blake2b. ~20µs per hit.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone

from .config import settings
from .detect import Classifier
from .fingerprint import make_fingerprint, name_from_digest
from .geo import Geo
from .geohash import encode as geohash
from .models import BotEvent, PortalVisit, SensorHit


_UA_HOST_RE = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})|[\w.+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def ua_host(ua: str) -> str:
    """Domain a bot publicly names in its User-Agent (contact URL or email). Public
    operator info, not personal data; lets the operator prove ownership via .well-known."""
    m = _UA_HOST_RE.search(ua or "")
    return (m.group(1) or m.group(2) or "").lower() if m else ""


class Enricher:
    def __init__(self, geo: Geo | None = None, classifier: Classifier | None = None):
        self.geo = geo or Geo()
        self.classifier = classifier or Classifier()

    def bot_event(self, hit: SensorHit, site: str) -> BotEvent | None:
        cls = self.classifier.classify(hit.ua, hit.headers)
        if not cls.is_bot:
            return None
        fp = make_fingerprint(hit.ua, hit.headers, hit.ip, hit.ja4, include_network=(cls.kind != "known"))
        digest = fp.digest
        g = self.geo.lookup(hit.ip, hit.cf_country)
        ts = hit.ts or time.time()
        return BotEvent(
            ts=ts,
            site=site,
            fp=digest,
            fam=fp.family_digest,
            name=name_from_digest(digest),
            ua_family=fp.ua_family,
            kind=cls.kind,
            operator=cls.operator,
            category=cls.category,
            label=cls.label,
            country=g.country,
            region=g.region,
            lat=g.lat,
            lon=g.lon,
            gh=geohash(g.lat, g.lon, 4),
            hit_robots=hit.path.rstrip("/").endswith("robots.txt"),
            ua_host=ua_host(hit.ua),
            homepage=cls.homepage,
        )

    def portal_visit(self, ip: str, ua: str, cf_country: str, page: str = "/") -> PortalVisit:
        """Human visit to the portal. The visitor hash is salted with the UTC
        date so the same person is one unique per day and unlinkable across
        days. The secret prefix stops offline precomputation."""
        ts = time.time()
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")
        material = f"{settings.visitor_secret}\x1f{day}\x1f{ip}\x1f{ua}".encode()
        visitor = hashlib.blake2b(material, digest_size=12).hexdigest()
        # Stable hash (no date) so "humans all time" counts a returning person once.
        # It only ever enters a HyperLogLog (irreversible, no per-visitor record).
        stable = hashlib.blake2b(f"{settings.visitor_secret}\x1fall\x1f{ip}\x1f{ua}".encode(), digest_size=12).hexdigest()
        g = self.geo.lookup(ip, cf_country)
        return PortalVisit(ts=ts, visitor=visitor, vs=stable, country=g.country, page=page[:64] or "/")
