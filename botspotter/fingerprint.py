"""Fingerprinting and naming.

A *fingerprint* identifies a bot (software + network), never a request.
The same bot gets the same name on every site, forever. If its software
changes, its fingerprint changes and it gets a new name; the aggregator
links the two as "evolved from".

What goes into the fingerprint (all stable across requests from one bot):
  - normalized User-Agent family (version numbers stripped)
  - the ordered list of header *names* the client sends (a strong signal
    of the HTTP library; values are NOT used)
  - the network prefix (/16 for IPv4, /32 for IPv6) — ONLY for unknown
    bots. It separates "python-requests from DigitalOcean" from
    "python-requests from Hetzner". Known crawlers (GPTBot, ClaudeBot…)
    already have a public identity, so one known bot = one name
    regardless of which data centre it crawls from.
  - optional TLS fingerprint (JA4) when the edge passes it as a header

The raw IP is hashed into the prefix and then discarded by the caller.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass

from .config import settings
from .names import ADJECTIVES, ANIMALS, NAME_SCHEME_VERSION

_VERSION_RE = re.compile(r"\d+(\.\d+)*")
_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"\+?https?://[^\s)]+|\+?[\w.-]+@[\w.-]+")  # contact URLs / emails
_EMPTY_PAREN_RE = re.compile(r"\(\s*[;,\s]*\)")
_TRAIL_RE = re.compile(r"[;,\s]+\)")

# Headers whose *presence* is meaningless noise added by proxies/CDNs.
_IGNORED_HEADERS = {
    "cf-connecting-ip", "cf-ipcountry", "cf-ray", "cf-visitor", "cdn-loop",
    "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip",
    "true-client-ip", "x-request-id", "x-amzn-trace-id", "via", "host",
    "content-length", "cookie", "authorization", "x-bs-site", "x-bs-ja4",
}


def normalize_user_agent(ua: str) -> str:
    """Strip version numbers, contact URLs and emails so GPTBot/1.1 and
    GPTBot/1.2 are the same family — but keep the parenthetical tokens,
    because "Mozilla/5.0 (compatible; ClaudeBot/1.0)" is identified by what
    is inside the parentheses."""
    if not ua:
        return "-"
    ua = _URL_RE.sub("", ua)
    ua = _VERSION_RE.sub("#", ua)
    ua = _TRAIL_RE.sub(")", ua)
    ua = _EMPTY_PAREN_RE.sub("", ua)
    ua = _WS_RE.sub(" ", ua).strip(" ;,").lower()
    return ua[:200] or "-"


def network_prefix(ip: str) -> str:
    """Coarse network key. Never store the input."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "net:unknown"
    if addr.version == 4:
        net = ipaddress.ip_network(f"{ip}/16", strict=False)
    else:
        net = ipaddress.ip_network(f"{ip}/32", strict=False)
    return f"net:{net.network_address}/{net.prefixlen}"


def header_signature(header_names: list[str]) -> str:
    names = [h.lower() for h in header_names if h and h.lower() not in _IGNORED_HEADERS]
    return ",".join(names)


@dataclass(frozen=True)
class Fingerprint:
    ua_family: str
    header_sig: str
    net_prefix: str
    ja4: str

    @property
    def digest(self) -> str:
        material = "\x1f".join([
            f"v{NAME_SCHEME_VERSION}",
            settings.naming_salt,
            self.ua_family,
            self.header_sig,
            self.net_prefix,
            self.ja4,
        ])
        return hashlib.blake2b(material.encode(), digest_size=16).hexdigest()

    @property
    def family_digest(self) -> str:
        """Hash of the UA family alone — used to link renamed bots
        ("evolved from") and to group bots per operator."""
        material = f"v{NAME_SCHEME_VERSION}\x1f{settings.naming_salt}\x1f{self.ua_family}"
        return hashlib.blake2b(material.encode(), digest_size=16).hexdigest()


def make_fingerprint(user_agent: str, header_names: list[str], ip: str, ja4: str = "",
                     include_network: bool = True) -> Fingerprint:
    return Fingerprint(
        ua_family=normalize_user_agent(user_agent),
        header_sig=header_signature(header_names),
        net_prefix=network_prefix(ip) if include_network else "net:-",
        ja4=(ja4 or "").strip().lower(),
    )


def name_from_digest(digest: str) -> str:
    """Deterministic 'Adjective Animal #NNNN'. 64*64*10000 = 40M names."""
    n = int(digest[:16], 16)
    adj = ADJECTIVES[n % len(ADJECTIVES)]
    n //= len(ADJECTIVES)
    animal = ANIMALS[n % len(ANIMALS)]
    n //= len(ANIMALS)
    num = n % 10000
    return f"{adj} {animal} #{num:04d}"


def slugify_name(name: str) -> str:
    """'Copper Heron #0042' -> 'copper-heron-0042' (URL-safe, searchable)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
