"""Check any site: what does its robots.txt ask AI crawlers to do, and is a given
IP really the bot it claims to be?

robots.txt is public, so this works for any domain — the result describes what the
site *asks* (RFC 9309), never what bots actually do; only enrolled sites get that.

Safety: we fetch a URL the visitor chose, so this is a server-side request forgery
surface. Every hostname is resolved first and all of its addresses must be public
(no loopback, private, link-local, multicast, reserved); redirects are followed by
hand with the same check per hop; 8 s timeout, 512 KB cap, no cookies; results
cached in Redis for an hour and checks rate-limited per visitor.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

import orjson

from .enroll import normalize_domain

CACHE_TTL = 3600
RATE_LIMIT = 40           # checks per visitor per hour
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 8
UA = "Mozilla/5.0 (compatible; BotSpotter-Check/1.0; +https://botspotter.app/check)"

# The AI crawlers people ask about, grouped by what the operator says the traffic is for.
# token = the product token sites put in robots.txt (matched case-insensitively).
AI_CRAWLERS: list[dict] = [
    {"token": "GPTBot",              "operator": "OpenAI",       "purpose": "training", "note": "trains OpenAI models"},
    {"token": "OAI-SearchBot",       "operator": "OpenAI",       "purpose": "search",   "note": "ChatGPT search index"},
    {"token": "ChatGPT-User",        "operator": "OpenAI",       "purpose": "agent",    "note": "fetches a page when a ChatGPT user asks"},
    {"token": "ClaudeBot",           "operator": "Anthropic",    "purpose": "training", "note": "trains Anthropic models"},
    {"token": "Claude-SearchBot",    "operator": "Anthropic",    "purpose": "search",   "note": "Claude search index"},
    {"token": "Claude-User",         "operator": "Anthropic",    "purpose": "agent",    "note": "fetches a page when a Claude user asks"},
    {"token": "Google-Extended",     "operator": "Google",       "purpose": "training", "note": "controls use for Gemini training (not a crawler itself)"},
    {"token": "Applebot-Extended",   "operator": "Apple",        "purpose": "training", "note": "controls use for Apple Intelligence training"},
    {"token": "PerplexityBot",       "operator": "Perplexity",   "purpose": "search",   "note": "Perplexity search index"},
    {"token": "Perplexity-User",     "operator": "Perplexity",   "purpose": "agent",    "note": "fetches a page when a Perplexity user asks"},
    {"token": "Bytespider",          "operator": "ByteDance",    "purpose": "training", "note": "TikTok/ByteDance models"},
    {"token": "CCBot",               "operator": "Common Crawl", "purpose": "training", "note": "open crawl dataset most LLMs trained on"},
    {"token": "meta-externalagent",  "operator": "Meta",         "purpose": "training", "note": "trains Meta models"},
    {"token": "Amazonbot",           "operator": "Amazon",       "purpose": "training", "note": "Alexa / Amazon models"},
    {"token": "DuckAssistBot",       "operator": "DuckDuckGo",   "purpose": "search",   "note": "DuckAssist answers"},
    {"token": "MistralAI-User",      "operator": "Mistral",      "purpose": "agent",    "note": "fetches a page when a Le Chat user asks"},
]
PURPOSE_LABEL = {"training": "Training crawlers", "search": "AI search", "agent": "AI agents (fetch for a live user)"}

# Bot name -> operator whose published ranges verify it (for the IP tool).
VERIFIABLE_BOTS: dict[str, str] = {
    "GPTBot": "OpenAI", "OAI-SearchBot": "OpenAI", "ChatGPT-User": "OpenAI",
    "Googlebot": "Google", "Google-Extended": "Google",
    "Bingbot": "Microsoft", "Applebot": "Apple", "Applebot-Extended": "Apple",
    "PerplexityBot": "Perplexity", "Perplexity-User": "Perplexity",
    "DuckDuckBot": "DuckDuckGo", "DuckAssistBot": "DuckDuckGo", "CCBot": "Common Crawl",
}


# ---------------------------------------------------------------- robots.txt (RFC 9309)
@dataclass
class Group:
    agents: list[str]
    rules: list[tuple[str, str]] = field(default_factory=list)     # (allow|disallow, path)


def parse_robots(text: str) -> list[Group]:
    """Groups of user-agent lines followed by allow/disallow rules. Tolerant of the
    real world: BOMs, CRLF, comments, 'User-Agent:' casing, tabs, blank lines inside groups."""
    groups: list[Group] = []
    cur: Group | None = None
    seen_rule = False
    for raw in text.lstrip("﻿").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            if cur is None or seen_rule:
                cur = Group([]); groups.append(cur); seen_rule = False
            cur.agents.append(val.lower())
        elif key in ("allow", "disallow"):
            if cur is None:              # rules before any user-agent line: apply to everyone
                cur = Group(["*"]); groups.append(cur)
            cur.rules.append((key, val)); seen_rule = True
        else:                            # sitemap, crawl-delay, ... end a group's rule run but keep it
            if key not in ("sitemap",):
                seen_rule = True
    return groups


def _rule_matches(pattern: str, path: str) -> bool:
    """RFC 9309 path matching: '*' wildcard, '$' end anchor, otherwise prefix."""
    if not pattern:
        return False
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    parts = [re.escape(p) for p in pattern.split("*")]
    rx = "^" + ".*".join(parts) + ("$" if anchored else "")
    return re.match(rx, path) is not None


def evaluate(groups: list[Group], token: str, path: str = "/") -> dict:
    """What the site asks `token` to do with `path`.
    status: allowed | blocked | partial ; via: named | wildcard | none (no robots.txt rules at all)"""
    t = token.lower()
    named = [g for g in groups if any(a == t for a in g.agents)]
    star = [g for g in groups if "*" in g.agents]
    use, via = (named, "named") if named else ((star, "wildcard") if star else ([], "none"))
    rules = [r for g in use for r in g.rules]
    # longest-match wins; allow beats disallow on a tie (RFC 9309 §2.2.2)
    best: tuple[int, str] | None = None
    for kind, pat in rules:
        if _rule_matches(pat, path):
            ln = len(pat.rstrip("$"))
            if best is None or ln > best[0] or (ln == best[0] and kind == "allow"):
                best = (ln, kind)
    root_blocked = best is not None and best[1] == "disallow"
    has_disallow = any(k == "disallow" and p for k, p in rules)
    status = "blocked" if root_blocked else ("partial" if has_disallow else "allowed")
    return {"status": status, "via": via, "rules": len(rules)}


def summarize(groups: list[Group], fetched: bool) -> dict:
    rows = []
    for c in AI_CRAWLERS:
        ev = evaluate(groups, c["token"]) if fetched else {"status": "allowed", "via": "none", "rules": 0}
        rows.append({**c, **ev})
    n = len(rows)
    blocked = sum(1 for r in rows if r["status"] == "blocked")
    partial = sum(1 for r in rows if r["status"] == "partial")
    named = sum(1 for r in rows if r["via"] == "named")
    training = [r for r in rows if r["purpose"] == "training"]
    tb = sum(1 for r in training if r["status"] == "blocked")
    by_purpose = {p: [r for r in rows if r["purpose"] == p] for p in PURPOSE_LABEL}
    if not fetched:
        headline = "No robots.txt — every AI crawler is allowed by default."
    elif blocked == n:
        headline = f"Blocks all {n} AI crawlers we check."
    elif blocked == 0 and partial == 0:
        headline = f"Allows all {n} AI crawlers we check" + (" — none of them is even mentioned." if named == 0 else ".")
    elif blocked == 0:
        headline = f"Blocks none of the {n} AI crawlers outright; {partial} are kept out of some paths · all {len(training)} training crawlers may use its content."
    else:
        headline = f"Blocks {blocked} of {n} AI crawlers" + (f", limits {partial} more to parts of the site" if partial else "") + \
                   f" · {len(training) - tb} of {len(training)} training crawlers may use its content."
    return {"rows": rows, "blocked": blocked, "partial": partial, "allowed": n - blocked - partial, "named": named,
            "training_allowed": len(training) - tb, "training_total": len(training), "headline": headline,
            "by_purpose": {p: {"label": PURPOSE_LABEL[p], "blocked": sum(1 for r in v if r["status"] == "blocked"), "total": len(v)}
                           for p, v in by_purpose.items()}}


def snippet(block: list[str]) -> str:
    """A robots.txt block the visitor can paste: block the chosen tokens for '/'."""
    lines = []
    for t in block:
        lines += [f"User-agent: {t}", "Disallow: /", ""]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- safe fetch
def _public_addresses(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    addrs = sorted({i[4][0] for i in infos})
    if not addrs:
        raise ValueError("does not resolve")
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if not ip.is_global or ip.is_multicast:
            raise ValueError("resolves to a non-public address")
    return addrs


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: D401
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _fetch_robots(domain: str) -> dict:
    """Returns {'url','status','text','final_host'}; raises ValueError with a human reason."""
    url = f"https://{domain}/robots.txt"
    tried_http = False
    for _ in range(MAX_REDIRECTS + 1):
        host = urllib.parse.urlsplit(url).hostname or ""
        _public_addresses(host)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/plain,*/*;q=0.5"})
        try:
            with _opener.open(req, timeout=TIMEOUT) as r:  # noqa: S310 - host validated above
                body = r.read(MAX_BYTES + 1)
                ctype = r.headers.get("content-type", "")
                return {"url": url, "status": r.status, "text": body[:MAX_BYTES].decode("utf-8", "replace"),
                        "truncated": len(body) > MAX_BYTES, "content_type": ctype}
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("location"):
                url = urllib.parse.urljoin(url, e.headers["location"])
                if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
                    raise ValueError("redirected to a non-http location") from None
                continue
            return {"url": url, "status": e.code, "text": "", "truncated": False, "content_type": ""}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if url.startswith("https://") and not tried_http:
                tried_http = True; url = "http://" + url[len("https://"):]; continue
            raise ValueError(f"could not reach {domain} ({getattr(e, 'reason', e)})") from None
    raise ValueError("too many redirects")


# ---------------------------------------------------------------- service
class Checker:
    def __init__(self, redis, verifier, fetch=None):
        self.r, self.verifier = redis, verifier
        self._fetch = fetch or (lambda d: asyncio.to_thread(_fetch_robots, d))

    async def allowed(self, visitor_ip: str) -> bool:
        key = f"bs:check:rl:{hashlib.sha256(visitor_ip.encode()).hexdigest()[:16]}"
        n = await self.r.incr(key)
        if n == 1:
            await self.r.expire(key, 3600)
        return n <= RATE_LIMIT

    async def robots(self, site: str) -> dict:
        domain = normalize_domain(site)
        if not domain:
            return {"error": "that does not look like a public domain name", "status": 400}
        ck = f"bs:check:robots:{domain}"
        cached = await self.r.get(ck)
        if cached:
            out = orjson.loads(cached); out["cached"] = True
            await self._count(domain)
            return out
        try:
            f = await self._fetch(domain)
        except ValueError as e:
            return {"error": str(e), "domain": domain, "status": 502}
        if f["status"] >= 500 or f["status"] == 429:
            # RFC 9309 §2.3.1.4: unreachable robots.txt = crawlers must assume full disallow for now; we cannot grade it
            return {"error": f"{domain} answered HTTP {f['status']} for robots.txt — temporarily unavailable, try again later",
                    "domain": domain, "status": 502}
        fetched = f["status"] == 200 and "html" not in f.get("content_type", "").lower()
        groups = parse_robots(f["text"]) if fetched else []
        s = summarize(groups, fetched)
        if not fetched and f["status"] not in (404, 410) and f["status"] != 200:
            s["headline"] = f"robots.txt answered HTTP {f['status']} — crawlers treat that as no restrictions (RFC 9309)."
        elif not fetched and f["status"] == 200:
            s["headline"] = "robots.txt is not a robots file (the site returned a web page) — crawlers treat that as no restrictions."
        out = {"domain": domain, "url": f["url"], "http_status": f["status"], "has_robots": fetched,
               "checked_at": time.time(), "cached": False, **s,
               "robots_excerpt": f["text"][:4000] if fetched else "",
               "snippet_block_training": snippet([r["token"] for r in s["rows"] if r["purpose"] == "training"]),
               "snippet_block_all": snippet([r["token"] for r in s["rows"]])}
        await self.r.set(ck, orjson.dumps(out), ex=CACHE_TTL)
        await self._count(domain)
        return out

    async def _count(self, domain: str) -> None:
        p = self.r.pipeline(transaction=False)
        p.zincrby("bs:check:top", 1, domain)
        p.incr("bs:check:count")
        await p.execute()

    async def popular(self, n: int = 8) -> list[dict]:
        rows = await self.r.zrevrange("bs:check:top", 0, n - 1, withscores=True)
        return [{"domain": d.decode() if isinstance(d, bytes) else d, "checks": int(s)} for d, s in rows]

    def ip(self, ip: str, bot: str) -> dict:
        bot_token = next((b for b in VERIFIABLE_BOTS if b.lower() == (bot or "").lower()), None)
        try:
            addr = ipaddress.ip_address(ip.strip())
        except ValueError:
            return {"error": "that is not an IP address", "status": 400}
        if bot_token is None:
            return {"error": "pick one of the bots we can verify", "bots": sorted(VERIFIABLE_BOTS), "status": 400}
        operator = VERIFIABLE_BOTS[bot_token]
        verdict = self.verifier.verdict(operator, str(addr))
        return {"ip": str(addr), "bot": bot_token, "operator": operator, "verdict": verdict or "unknown",
                "ranges_known": operator in self.verifier.nets, "ranges_updated": self.verifier.updated,
                "bots": sorted(VERIFIABLE_BOTS)}


def crawler_catalog() -> dict:
    return {"crawlers": [dict(c) for c in AI_CRAWLERS], "verifiable": sorted(VERIFIABLE_BOTS)}


__all__ = ["AI_CRAWLERS", "VERIFIABLE_BOTS", "Checker", "Group", "crawler_catalog", "evaluate", "parse_robots",
           "snippet", "summarize", "asdict"]
