"""Claim your bot: prove you operate a bot, then manage its public identity.

Why a claim needs proof
    A name is public; anyone could claim "Copper Heron #0042". The fingerprint
    is the bot's identity, and only the operator can make the bot send a
    request. So verification is a challenge the *bot* must answer.

Two verification methods
    ``fetch``      — the claimant makes their bot request
                     ``https://botspotter.app/verify/<token>``. The request is
                     fingerprinted exactly like any sensor hit; if the digest
                     equals the claimed bot's ``fp``, the claim is verified.
                     Works for any bot, including unidentified ones.
    ``wellknown``  — the claimant publishes ``botspotter-verify=<token>`` at
                     ``https://<homepage>/.well-known/botspotter-verify.txt``
                     where ``<homepage>`` is the bot's registered homepage (known
                     bots) or a domain named in the bot's User-Agent. We fetch it.

After verification the claimant receives an ``owner_key`` (shown once, hashed
in Redis). With it they can rename the bot and set description, homepage,
operator and category via ``PATCH /v1/bots/{slug}``. The generated name is
kept as a permanent alias so old links and searches keep working.

Redis keys
    bs:claim:<token>        HASH fp, slug, email, method, status, created, verified, host
    bs:bot:<fp>             + fields owner_hash, display_name, display_slug, desc,
                            homepage, claimed_at, claimed_contact (hashed email)
    bs:name:<display_slug>  -> fp   (so search / /bot/<slug> work for the new name)
    bs:claims:ip:<hash>     rate limit (5 claims / hour / IP)
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

import orjson

from .config import settings
from .fingerprint import slugify_name

CLAIM_TTL_S = 7 * 86400
CLAIMS_PER_IP_PER_HOUR = 5
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'#\-_]{1,38}[A-Za-z0-9.]$")
ALLOWED_CATEGORIES = {"training", "search", "agent", "scraper", "seo", "other"}
RESERVED_SLUGS = {"about", "sensors", "terms", "claim", "verify", "api", "v1", "static", "bot", "admin", "botspotter"}


def _h(s: str) -> str:
    return hashlib.blake2b(f"{settings.visitor_secret}\x1f{s}".encode(), digest_size=16).hexdigest()


@dataclass
class ClaimResult:
    ok: bool
    status: int
    body: dict


def host_for_wellknown(bot: dict) -> str:
    """Domain a 'wellknown' challenge may be served from, in order of trust:
    registry homepage (known bots), else the domain the bot names in its own
    User-Agent (recorded at first sight as ua_host)."""
    hp = bot.get("registry_homepage") or ""
    if hp:
        return (urllib.parse.urlparse(hp).hostname or "").lower()
    return (bot.get("ua_host") or "").lower()


def fetch_wellknown(host: str, timeout: float = 6.0) -> str:
    """GET https://<host>/.well-known/botspotter-verify.txt (sync; call in a thread)."""
    req = urllib.request.Request(
        f"https://{host}/.well-known/botspotter-verify.txt",
        headers={"User-Agent": "BotSpotter-Verify/1.0 (+https://botspotter.app/claim)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - host is the bot's own declared domain
        return r.read(4096).decode("utf-8", "replace")


class Claims:
    def __init__(self, counters, wellknown_fetcher=fetch_wellknown):
        self.c = counters
        self.r = counters.r
        self._fetch = wellknown_fetcher

    # ------------------------------------------------------------ create
    async def create(self, slug: str, email: str, method: str, client_ip: str, base_url: str = "https://botspotter.app") -> ClaimResult:
        if method not in ("fetch", "wellknown"):
            return ClaimResult(False, 400, {"error": "method must be 'fetch' or 'wellknown'"})
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""):
            return ClaimResult(False, 400, {"error": "a valid email is required"})
        rl_key = f"bs:claims:ip:{_h(client_ip)}"
        n = await self.r.incr(rl_key)
        if n == 1:
            await self.r.expire(rl_key, 3600)
        if n > CLAIMS_PER_IP_PER_HOUR:
            return ClaimResult(False, 429, {"error": "too many claims from this address; try again in an hour"})
        bot = await self.c.bot_by_slug(slug.lower())
        if bot is None:
            return ClaimResult(False, 404, {"error": "no bot by that name"})
        if bot.get("claimed_at"):
            return ClaimResult(False, 409, {"error": "this bot is already claimed; email hello@botspotter.app to dispute"})
        host = ""
        if method == "wellknown":
            host = host_for_wellknown(bot)
            if not host:
                return ClaimResult(False, 400, {"error": "this bot has no homepage or domain in its User-Agent; use the 'fetch' method"})
        token = secrets.token_urlsafe(24)
        await self.r.hset(f"bs:claim:{token}", mapping={
            "fp": bot["fp"], "slug": bot["slug"], "email": email, "method": method,
            "status": "pending", "created": time.time(), "host": host,
        })
        await self.r.expire(f"bs:claim:{token}", CLAIM_TTL_S)
        instructions = (
            {"method": "fetch",
             "verify_url": f"{base_url.rstrip('/')}/verify/{token}",
             "how": "Make the bot you are claiming request the verify_url (GET). It must send the same "
                    "User-Agent and headers it normally sends. Any one request is enough. Then poll status."}
            if method == "fetch" else
            {"method": "wellknown",
             "host": host,
             "file_url": f"https://{host}/.well-known/botspotter-verify.txt",
             "content": f"botspotter-verify={token}",
             "how": "Publish the content at file_url, then call POST /v1/claims/<token>/check."}
        )
        return ClaimResult(True, 201, {"token": token, "bot": {"name": bot["name"], "slug": bot["slug"]},
                                       "expires_in_s": CLAIM_TTL_S, **instructions})

    # ------------------------------------------------------------ verify
    async def _load(self, token: str) -> dict | None:
        raw = await self.r.hgetall(f"bs:claim:{token}")
        return {k.decode(): v.decode() for k, v in raw.items()} if raw else None

    async def verify_fetch(self, token: str, request_fp: str | None) -> ClaimResult:
        """Called for GET /verify/<token>. request_fp is the fingerprint of *this*
        request (None if the request was classified as a human browser)."""
        cl = await self._load(token)
        if cl is None:
            return ClaimResult(False, 404, {"verified": False, "error": "unknown or expired token"})
        if cl["status"] == "verified":
            return ClaimResult(True, 200, {"verified": True, "note": "already verified"})
        if cl["method"] != "fetch":
            return ClaimResult(False, 400, {"verified": False, "error": "this claim uses the well-known method"})
        if request_fp is None:
            return ClaimResult(False, 403, {"verified": False, "error": "this request looks like a browser, not a bot — make the bot itself fetch this URL"})
        if request_fp != cl["fp"]:
            await self.r.hset(f"bs:claim:{token}", mapping={"last_mismatch": time.time(), "attempts": int(cl.get("attempts", 0)) + 1})
            return ClaimResult(False, 403, {"verified": False, "error": "fingerprint does not match the claimed bot (different User-Agent, headers, or network)"})
        await self._mark_verified(token, cl)
        return ClaimResult(True, 200, {"verified": True})

    async def check_wellknown(self, token: str) -> ClaimResult:
        import asyncio

        cl = await self._load(token)
        if cl is None:
            return ClaimResult(False, 404, {"verified": False, "error": "unknown or expired token"})
        if cl["status"] == "verified":
            return ClaimResult(True, 200, {"verified": True, "note": "already verified"})
        if cl["method"] != "wellknown":
            return ClaimResult(False, 400, {"verified": False, "error": "this claim uses the fetch method"})
        try:
            body = await asyncio.to_thread(self._fetch, cl["host"])
        except Exception as exc:  # noqa: BLE001
            return ClaimResult(False, 424, {"verified": False, "error": f"could not fetch https://{cl['host']}/.well-known/botspotter-verify.txt ({exc.__class__.__name__})"})
        if f"botspotter-verify={token}" not in body:
            return ClaimResult(False, 403, {"verified": False, "error": "file fetched but the token line was not found"})
        await self._mark_verified(token, cl)
        return ClaimResult(True, 200, {"verified": True})

    async def _mark_verified(self, token: str, cl: dict) -> None:
        owner_key = secrets.token_urlsafe(24)
        now = time.time()
        p = self.r.pipeline(transaction=False)
        p.hset(f"bs:claim:{token}", mapping={"status": "verified", "verified": now, "owner_key": owner_key})
        p.hset(f"bs:bot:{cl['fp']}", mapping={
            "owner_hash": _h(owner_key), "claimed_at": now, "claimed_contact": _h(cl["email"]),
            "claim_method": cl["method"],
        })
        p.lpush("bs:claims:log", orjson.dumps({"ts": now, "fp": cl["fp"], "slug": cl["slug"], "method": cl["method"]}))
        p.ltrim("bs:claims:log", 0, 499)
        await p.execute()

    async def status(self, token: str) -> ClaimResult:
        cl = await self._load(token)
        if cl is None:
            return ClaimResult(False, 404, {"error": "unknown or expired token"})
        out = {"status": cl["status"], "slug": cl["slug"], "method": cl["method"], "attempts": int(cl.get("attempts", 0))}
        if cl["status"] == "verified":
            # The owner key is handed out to whoever holds the claim token (the claimant).
            out["owner_key"] = cl["owner_key"]
            out["manage"] = f"/claim?slug={cl['slug']}"
        return ClaimResult(True, 200, out)

    # ------------------------------------------------------------ manage
    async def authorize(self, slug: str, owner_key: str) -> dict | None:
        bot = await self.c.bot_by_slug(slug.lower())
        if not bot or not owner_key:
            return None
        owner_hash = await self.r.hget(f"bs:bot:{bot['fp']}", "owner_hash")
        if not owner_hash or owner_hash.decode() != _h(owner_key):
            return None
        return bot

    async def update(self, bot: dict, patch: dict) -> ClaimResult:
        fp = bot["fp"]
        fields: dict[str, str] = {}
        if "display_name" in patch:
            name = (patch["display_name"] or "").strip()
            if name == "":
                # clear the custom name
                old = await self.r.hget(f"bs:bot:{fp}", "display_slug")
                if old:
                    await self.r.delete(f"bs:name:{old.decode()}")
                    await self.r.zrem("bs:names", old.decode())
                await self.r.hdel(f"bs:bot:{fp}", "display_name", "display_slug")
            else:
                if not NAME_RE.match(name):
                    return ClaimResult(False, 400, {"error": "name: 3–40 chars, letters/numbers/space/.#-'_ only"})
                new_slug = slugify_name(name)
                if new_slug in RESERVED_SLUGS:
                    return ClaimResult(False, 400, {"error": "that name is reserved"})
                owner = await self.r.get(f"bs:name:{new_slug}")
                if owner and owner.decode() != fp:
                    return ClaimResult(False, 409, {"error": "that name is already taken"})
                old = await self.r.hget(f"bs:bot:{fp}", "display_slug")
                p = self.r.pipeline(transaction=False)
                if old and old.decode() != new_slug:
                    p.delete(f"bs:name:{old.decode()}")
                    p.zrem("bs:names", old.decode())
                p.set(f"bs:name:{new_slug}", fp)
                p.zadd("bs:names", {new_slug: 0})
                await p.execute()
                fields["display_name"] = name
                fields["display_slug"] = new_slug
        if "description" in patch:
            fields["desc"] = re.sub(r"\s+", " ", str(patch["description"] or ""))[:280]
        if "homepage" in patch:
            hp = str(patch["homepage"] or "").strip()
            if hp and not re.match(r"^https?://[^\s]+$", hp):
                return ClaimResult(False, 400, {"error": "homepage must be an http(s) URL"})
            fields["homepage"] = hp[:200]
        if "operator" in patch:
            op = re.sub(r"[^\w .&'\-]", "", str(patch["operator"] or ""))[:60].strip()
            if op:
                fields["operator"] = op
        if "category" in patch:
            cat = str(patch["category"] or "").lower()
            if cat not in ALLOWED_CATEGORIES:
                return ClaimResult(False, 400, {"error": f"category must be one of {sorted(ALLOWED_CATEGORIES)}"})
            fields["category"] = cat
        if "contact_url" in patch:
            fields["contact_url"] = str(patch["contact_url"] or "").strip()[:200]
        if fields:
            fields["updated_at"] = str(time.time())
            await self.r.hset(f"bs:bot:{fp}", mapping=fields)
        return ClaimResult(True, 200, {"ok": True, "updated": sorted(fields)})
