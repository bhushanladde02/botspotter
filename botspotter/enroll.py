"""Self-serve sensor enrollment: sign in, prove you own a domain, get a key.

Why authentication matters: a sensor key lets its holder inject "bot visits"
for a site. Keys are therefore issued only to an authenticated user who has
proven control of the domain — exactly the two things a human reviewer used to
check by email, now automatic.

Identity: GitHub OAuth (site owners are developers; no e-mail infrastructure
needed). We store the GitHub numeric id, login and avatar URL — nothing else.

Ownership proof (either one, like bot claims):
  dns       TXT record  _botspotter.<domain>  ->  botspotter-verify=<token>
  wellknown https://<domain>/.well-known/botspotter-verify.txt containing that line

On success a sensor key is generated, shown ONCE, and stored only as its
sha256 in ``bs:sensor_keys`` (hash -> site id), which ingest consults for keys
that are not in BS_SENSOR_KEYS. Rotating replaces the hash atomically.

Redis layout
  bs:user:<gh_id>            HASH login, name, avatar, created, last_seen
  bs:user:<gh_id>:sites      SET  domains
  bs:site:<domain>           HASH owner, status (pending|verified), token, created, verified,
                                  key_hash, lat, lon, city, country
  bs:sensor_keys             HASH sha256(key) -> domain  (read by ingest)
  bs:enroll:ratelimit:<id>   per-user verification attempts (10 / hour)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import socket
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Awaitable, Callable

import orjson
from redis import asyncio as aioredis

from .config import settings

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
MAX_SITES_PER_USER = 5
VERIFY_ATTEMPTS_PER_HOUR = 10
TOKEN_TTL_S = 7 * 86400
SESSION_TTL_S = 30 * 86400


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def normalize_domain(raw: str) -> str | None:
    d = (raw or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0].split(":")[0]
    d = d[4:] if d.startswith("www.") else d
    return d if DOMAIN_RE.match(d) else None


# ---------------------------------------------------------------- sessions
class Sessions:
    """Signed, stateless session cookie: <gh_id>.<expiry>.<hmac>. No server-side
    session store; revocation = rotate BS_SESSION_SECRET."""

    def __init__(self, secret: str):
        self.secret = secret.encode()

    def issue(self, gh_id: int) -> str:
        exp = int(time.time()) + SESSION_TTL_S
        body = f"{gh_id}.{exp}"
        return body + "." + hmac.new(self.secret, body.encode(), "sha256").hexdigest()[:32]

    def read(self, cookie: str | None) -> int | None:
        if not cookie:
            return None
        try:
            gh_id, exp, sig = cookie.split(".")
            body = f"{gh_id}.{exp}"
            if not hmac.compare_digest(sig, hmac.new(self.secret, body.encode(), "sha256").hexdigest()[:32]):
                return None
            if int(exp) < time.time():
                return None
            return int(gh_id)
        except (ValueError, AttributeError):
            return None


# ---------------------------------------------------------------- GitHub OAuth
@dataclass
class GitHubUser:
    id: int
    login: str
    name: str
    avatar: str


Exchanger = Callable[[str], Awaitable[GitHubUser]]


async def github_exchange(code: str) -> GitHubUser:
    """Code -> access token -> public profile. Standard GitHub OAuth, no scopes
    (we never read repos, e-mail or anything private)."""
    def _post_token() -> dict:
        data = urllib.parse.urlencode({"client_id": settings.github_client_id, "client_secret": settings.github_client_secret,
                                       "code": code}).encode()
        req = urllib.request.Request("https://github.com/login/oauth/access_token", data=data,
                                     headers={"Accept": "application/json", "User-Agent": "BotSpotter"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return orjson.loads(r.read())

    def _get_user(token: str) -> dict:
        req = urllib.request.Request("https://api.github.com/user",
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                              "User-Agent": "BotSpotter"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return orjson.loads(r.read())

    tok = await asyncio.to_thread(_post_token)
    access = tok.get("access_token")
    if not access:
        raise PermissionError(tok.get("error_description") or "GitHub did not return a token")
    u = await asyncio.to_thread(_get_user, access)
    return GitHubUser(int(u["id"]), str(u.get("login") or ""), str(u.get("name") or ""), str(u.get("avatar_url") or ""))


# ---------------------------------------------------------------- ownership proofs
async def dns_txt(domain: str) -> list[str]:
    """TXT records of _botspotter.<domain>. dnspython if present (the production
    image has it); otherwise an empty list so the well-known route still works."""
    try:
        import dns.asyncresolver  # type: ignore
    except ImportError:  # pragma: no cover
        return []
    try:
        ans = await dns.asyncresolver.resolve(f"_botspotter.{domain}", "TXT", lifetime=5)
        return ["".join(s.decode() if isinstance(s, bytes) else s for s in r.strings) for r in ans]
    except Exception:  # noqa: BLE001 - NXDOMAIN, timeout, ... all mean "not proven"
        return []


def _fetch_wellknown(domain: str) -> str:
    req = urllib.request.Request(f"https://{domain}/.well-known/botspotter-verify.txt",
                                 headers={"User-Agent": "BotSpotter verification (+https://botspotter.app/sensors)"})
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310 - host is the user's own claimed domain
        return r.read(4096).decode("utf-8", "replace")


async def wellknown_text(domain: str) -> str:
    try:
        return await asyncio.to_thread(_fetch_wellknown, domain)
    except Exception:  # noqa: BLE001
        return ""


async def locate(domain: str, geo) -> dict:
    """City-level location of the site from its A record + GeoIP (for the map's
    arc destination). Falls back to (0,0) = 'unknown' — the map ripples instead."""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, domain, 443, socket.AF_INET)
        ip = infos[0][4][0]
    except Exception:  # noqa: BLE001
        return {"lat": 0.0, "lon": 0.0, "city": "", "country": "XX"}
    g = geo.lookup(ip, "")
    return {"lat": g.lat, "lon": g.lon, "city": g.city, "country": g.country}


# ---------------------------------------------------------------- store
@dataclass
class Result:
    ok: bool
    status: int
    body: dict


class Enrollment:
    def __init__(self, r: aioredis.Redis, geo, dns=dns_txt, wellknown=wellknown_text):
        self.r = r
        self.geo = geo
        self._dns = dns
        self._wellknown = wellknown

    # users
    async def upsert_user(self, u: GitHubUser) -> None:
        now = time.time()
        p = self.r.pipeline(transaction=False)
        p.hsetnx(f"bs:user:{u.id}", "created", now)
        p.hset(f"bs:user:{u.id}", mapping={"login": u.login, "name": u.name, "avatar": u.avatar, "last_seen": now})
        await p.execute()

    async def user(self, gh_id: int) -> dict | None:
        m = await self.r.hgetall(f"bs:user:{gh_id}")
        if not m:
            return None
        d = {k.decode(): v.decode() for k, v in m.items()}
        return {"id": gh_id, "login": d.get("login", ""), "name": d.get("name", ""), "avatar": d.get("avatar", "")}

    # sites
    async def my_sites(self, gh_id: int) -> list[dict]:
        domains = sorted(x.decode() for x in await self.r.smembers(f"bs:user:{gh_id}:sites"))
        out = []
        for d in domains:
            s = await self._site(d)
            if s and s["owner"] == str(gh_id):
                hits = await self.r.zscore("bs:sensors", d)
                last = await self.r.hget(f"bs:sensor:{d}", "last_seen")
                out.append({"domain": d, "status": s["status"], "created": float(s.get("created", 0)),
                            "verified": float(s["verified"]) if s.get("verified") else None,
                            "hits": int(hits or 0), "last_seen": float(last) if last else None,
                            "city": s.get("city", ""), "token": s["token"] if s["status"] == "pending" else None})
        return out

    async def _site(self, domain: str) -> dict | None:
        m = await self.r.hgetall(f"bs:site:{domain}")
        return {k.decode(): v.decode() for k, v in m.items()} if m else None

    async def add_site(self, gh_id: int, raw_domain: str) -> Result:
        domain = normalize_domain(raw_domain)
        if not domain:
            return Result(False, 400, {"error": "that doesn't look like a domain (example.com)"})
        existing = await self._site(domain)
        if existing and existing["owner"] != str(gh_id):
            return Result(False, 409, {"error": "this domain is already enrolled by another account"})
        if existing and existing["status"] == "verified":
            return Result(True, 200, {"domain": domain, "status": "verified", "note": "already verified"})
        n = await self.r.scard(f"bs:user:{gh_id}:sites")
        if not existing and n >= MAX_SITES_PER_USER:
            return Result(False, 429, {"error": f"limit of {MAX_SITES_PER_USER} sites per account — contact us for more"})
        token = existing["token"] if existing else secrets.token_urlsafe(18)
        p = self.r.pipeline(transaction=False)
        p.hset(f"bs:site:{domain}", mapping={"owner": gh_id, "status": "pending", "token": token, "created": time.time()})
        p.sadd(f"bs:user:{gh_id}:sites", domain)
        await p.execute()
        return Result(True, 201, {"domain": domain, "status": "pending", "token": token, **self.instructions(domain, token)})

    @staticmethod
    def instructions(domain: str, token: str) -> dict:
        return {
            "dns": {"name": f"_botspotter.{domain}", "type": "TXT", "value": f"botspotter-verify={token}"},
            "wellknown": {"url": f"https://{domain}/.well-known/botspotter-verify.txt", "content": f"botspotter-verify={token}"},
        }

    async def verify(self, gh_id: int, domain: str) -> Result:
        s = await self._site(domain)
        if not s or s["owner"] != str(gh_id):
            return Result(False, 404, {"error": "unknown site"})
        if s["status"] == "verified":
            return Result(True, 200, {"domain": domain, "status": "verified", "note": "already verified"})
        rl = f"bs:enroll:ratelimit:{gh_id}"
        n = await self.r.incr(rl)
        if n == 1:
            await self.r.expire(rl, 3600)
        if n > VERIFY_ATTEMPTS_PER_HOUR:
            return Result(False, 429, {"error": "too many verification attempts; try again in an hour"})
        want = f"botspotter-verify={s['token']}"
        via = ""
        if any(want in t for t in await self._dns(domain)):
            via = "dns"
        elif want in await self._wellknown(domain):
            via = "wellknown"
        if not via:
            return Result(False, 403, {"verified": False, "error": "proof not found yet — DNS can take a few minutes to propagate; "
                                                                   "the file must be served over https at exactly that path",
                                       **self.instructions(domain, s["token"])})
        key = secrets.token_hex(16)
        loc = await locate(domain, self.geo)
        now = time.time()
        p = self.r.pipeline(transaction=False)
        p.hset(f"bs:site:{domain}", mapping={"status": "verified", "verified": now, "via": via, "key_hash": _h(key), **loc})
        p.hset("bs:sensor_keys", _h(key), domain)
        await p.execute()
        return Result(True, 200, {"domain": domain, "status": "verified", "via": via, "key": key, "location": loc,
                                  "note": "this key is shown once; rotate it from My sites if you lose it"})

    async def rotate_key(self, gh_id: int, domain: str) -> Result:
        s = await self._site(domain)
        if not s or s["owner"] != str(gh_id) or s["status"] != "verified":
            return Result(False, 404, {"error": "unknown or unverified site"})
        key = secrets.token_hex(16)
        p = self.r.pipeline(transaction=False)
        if s.get("key_hash"):
            p.hdel("bs:sensor_keys", s["key_hash"])
        p.hset("bs:sensor_keys", _h(key), domain)
        p.hset(f"bs:site:{domain}", mapping={"key_hash": _h(key), "rotated": time.time()})
        await p.execute()
        return Result(True, 200, {"domain": domain, "key": key, "note": "old key stops working immediately"})

    async def remove_site(self, gh_id: int, domain: str) -> Result:
        s = await self._site(domain)
        if not s or s["owner"] != str(gh_id):
            return Result(False, 404, {"error": "unknown site"})
        p = self.r.pipeline(transaction=False)
        if s.get("key_hash"):
            p.hdel("bs:sensor_keys", s["key_hash"])
        p.delete(f"bs:site:{domain}")
        p.srem(f"bs:user:{gh_id}:sites", domain)
        await p.execute()
        return Result(True, 200, {"domain": domain, "removed": True, "note": "the key is revoked; public statistics already published stay"})

    # read side used by ingest (dynamic keys) and the map (dynamic site locations)
    async def site_for_key(self, key: str) -> str | None:
        v = await self.r.hget("bs:sensor_keys", _h(key))
        return v.decode() if v else None

    async def site_location(self, domain: str) -> dict | None:
        s = await self._site(domain)
        if not s or s["status"] != "verified":
            return None
        return {"lat": float(s.get("lat", 0) or 0), "lon": float(s.get("lon", 0) or 0), "city": s.get("city", "")}

    async def public_sites(self) -> list[dict]:
        out = []
        async for k in self.r.scan_iter(match="bs:site:*", count=200):
            d = k.decode().split(":", 2)[2]
            s = await self._site(d)
            if s and s["status"] == "verified":
                out.append({"site": d, "city": s.get("city", ""), "lat": float(s.get("lat", 0) or 0),
                            "lon": float(s.get("lon", 0) or 0), "url": f"https://{d}"})
        return out
