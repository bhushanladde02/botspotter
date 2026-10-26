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
from .verify import Verifier


_UA_HOST_RE = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})|[\w.+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def ua_host(ua: str) -> str:
    """Domain a bot publicly names in its User-Agent (contact URL or email). Public
    operator info, not personal data; lets the operator prove ownership via .well-known."""
    m = _UA_HOST_RE.search(ua or "")
    return (m.group(1) or m.group(2) or "").lower() if m else ""


_SOURCE_ALIASES = {
    "t.co": "x.com", "twitter.com": "x.com", "lnkd.in": "linkedin.com", "l.facebook.com": "facebook.com",
    "m.facebook.com": "facebook.com", "out.reddit.com": "reddit.com", "old.reddit.com": "reddit.com",
    "news.ycombinator.com": "hacker news", "com.linkedin.android": "linkedin.com",
}


def referrer_source(referrer: str) -> str:
    """Reduce a Referer header to a source label: the registrable-ish host with
    common redirectors folded (t.co -> x.com). Empty -> 'direct'. Own domain ->
    'internal' (ignored by the aggregator)."""
    if not referrer:
        return "direct"
    try:
        from urllib.parse import urlparse

        host = (urlparse(referrer).hostname or "").lower()
    except Exception:
        return "direct"
    if not host:
        return "direct"
    if host.endswith("botspotter.app") or host in ("localhost", "127.0.0.1"):
        return "internal"
    host = host[4:] if host.startswith("www.") else host
    return _SOURCE_ALIASES.get(host, host)[:60]


_BROWSERS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"), ("Firefox/", "Firefox"),
             ("Chrome/", "Chrome"), ("CriOS/", "Chrome"), ("FxiOS/", "Firefox"), ("Safari/", "Safari"))
_OSES = (("Windows", "Windows"), ("CrOS", "ChromeOS"), ("Android", "Android"), ("iPhone", "iOS"), ("iPad", "iPadOS"),
         ("Mac OS X", "macOS"), ("Linux", "Linux"))


def ua_client(ua: str) -> tuple[str, str, str]:
    """(browser family, OS family, device class) from a browser User-Agent.
    Deliberately coarse — no versions — it is for audience statistics, not identification."""
    ua = ua or ""
    browser = next((b for k, b in _BROWSERS if k in ua), "other")
    os_ = next((o for k, o in _OSES if k in ua), "other")
    if "iPad" in ua or ("Android" in ua and "Mobile" not in ua):
        device = "tablet"
    elif "Mobile" in ua or "iPhone" in ua or "Android" in ua:
        device = "mobile"
    else:
        device = "desktop"
    return browser, os_, device


def first_language(accept_language: str) -> str:
    tag = (accept_language or "").split(",")[0].split(";")[0].strip()
    return tag[:12] if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", tag) else ""


class Enricher:
    def __init__(self, geo: Geo | None = None, classifier: Classifier | None = None, verifier: Verifier | None = None):
        self.geo = geo or Geo()
        self.classifier = classifier or Classifier()
        self.verifier = verifier or Verifier()

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
            city=g.city,
            org=g.org[:80],
            cloud=g.cloud,
            verified=self.verifier.verdict(cls.operator, hit.ip) if cls.kind == "known" else "",
        )

    def portal_visit(self, ip: str, ua: str, cf_country: str, page: str = "/", referrer: str = "",
                     city: str = "", region: str = "", accept_language: str = "") -> PortalVisit:
        """Human visit to the portal. The visitor hash is salted with the UTC
        date so the same person is one unique per day and unlinkable across
        days. The secret prefix stops offline precomputation."""
        ts = time.time()
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")
        material = f"{settings.visitor_secret}\x1f{day}\x1f{ip}\x1f{ua}".encode()
        visitor = hashlib.blake2b(material, digest_size=12).hexdigest()
        # Stable hash (no date) so "humans all time" counts a returning person once and
        # the owner-only audience ledger can list a returning visitor as one row.
        stable = hashlib.blake2b(f"{settings.visitor_secret}\x1fall\x1f{ip}\x1f{ua}".encode(), digest_size=12).hexdigest()
        g = self.geo.lookup(ip, cf_country)
        browser, os_, device = ua_client(ua)
        return PortalVisit(ts=ts, visitor=visitor, vs=stable, country=g.country, page=page[:64] or "/",
                           ref=referrer_source(referrer), city=(city or "")[:64], region=(region or g.region or "")[:32],
                           browser=browser, os=os_, device=device, lang=first_language(accept_language))
