"""Check any site: robots.txt parsing against real-world quirks, the AI-crawler
summary, the SSRF guard, the per-visitor rate limit, caching, and the IP verifier."""
import httpx
import pytest

from botspotter.api.app import create_app as create_api
from botspotter.check import (AI_CRAWLERS, RATE_LIMIT, Checker, _public_addresses, evaluate, parse_robots,
                              snippet, summarize)
from botspotter.verify import Verifier

NYT_LIKE = """﻿# comment line
User-agent: *
Disallow: /ads/
Allow: /ads/public/

User-Agent: GPTBot
Disallow: /

user-agent: ClaudeBot
user-agent: anthropic-ai
Disallow: /
Crawl-delay: 10

User-agent: PerplexityBot
Allow: /

Sitemap: https://example.com/sitemap.xml
"""


def test_parse_handles_bom_casing_crlf_and_shared_groups():
    groups = parse_robots(NYT_LIKE.replace("\n", "\r\n"))
    assert [g.agents for g in groups] == [["*"], ["gptbot"], ["claudebot", "anthropic-ai"], ["perplexitybot"]]
    assert groups[0].rules == [("disallow", "/ads/"), ("allow", "/ads/public/")]
    assert groups[2].rules == [("disallow", "/")]


def test_evaluate_named_beats_wildcard_and_longest_match_wins():
    g = parse_robots(NYT_LIKE)
    assert evaluate(g, "GPTBot") == {"status": "blocked", "via": "named", "rules": 1}
    assert evaluate(g, "claudebot")["status"] == "blocked"                       # case-insensitive, shared group
    assert evaluate(g, "PerplexityBot") == {"status": "allowed", "via": "named", "rules": 1}
    assert evaluate(g, "Bytespider") == {"status": "partial", "via": "wildcard", "rules": 2}   # /ads/ only
    assert evaluate(g, "Bytespider", "/ads/x") ["status"] == "blocked"
    assert evaluate(g, "Bytespider", "/ads/public/x")["status"] == "partial"  # longer Allow wins for that path
    assert evaluate([], "GPTBot") == {"status": "allowed", "via": "none", "rules": 0}
    # wildcard + end anchor
    g2 = parse_robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /private*\n")
    assert evaluate(g2, "CCBot", "/a/b.pdf")["status"] == "blocked"
    assert evaluate(g2, "CCBot", "/a/b.pdfx")["status"] == "partial"
    assert evaluate(g2, "CCBot", "/private-stuff")["status"] == "blocked"


def test_summary_headlines_and_snippet():
    s = summarize(parse_robots(NYT_LIKE), True)
    assert s["blocked"] == 2 and s["named"] == 3 and s["headline"].startswith("Blocks 2 of")
    assert s["training_total"] == sum(1 for c in AI_CRAWLERS if c["purpose"] == "training")
    assert summarize([], False)["headline"].startswith("No robots.txt")
    assert summarize(parse_robots("User-agent: *\nDisallow: /\n"), True)["headline"].startswith("Blocks all")
    assert summarize(parse_robots("User-agent: *\nAllow: /\n"), True)["headline"].endswith("is even mentioned.")
    assert snippet(["GPTBot", "CCBot"]) == "User-agent: GPTBot\nDisallow: /\n\nUser-agent: CCBot\nDisallow: /\n"


def test_ssrf_guard_rejects_non_public_hosts():
    for host in ("localhost", "127.0.0.1", "10.0.0.5", "169.254.169.254", "[::1]", "0.0.0.0"):
        with pytest.raises((ValueError, OSError)):
            _public_addresses(host.strip("[]"))


FAKE = {
    "nyt.test": {"url": "https://nyt.test/robots.txt", "status": 200, "text": NYT_LIKE, "truncated": False, "content_type": "text/plain"},
    "open.test": {"url": "https://open.test/robots.txt", "status": 404, "text": "", "truncated": False, "content_type": ""},
    "spa.test": {"url": "https://spa.test/robots.txt", "status": 200, "text": "<html>app</html>", "truncated": False, "content_type": "text/html"},
    "forbid.test": {"url": "https://forbid.test/robots.txt", "status": 403, "text": "", "truncated": False, "content_type": ""},
    "broken.test": {"url": "https://broken.test/robots.txt", "status": 503, "text": "", "truncated": False, "content_type": ""},
    "pypi.test": {"url": "https://pypi.test/robots.txt", "status": 200, "text": "User-agent: *\nDisallow: /search\n", "truncated": False, "content_type": "text/plain"},
}
calls: list[str] = []


async def fake_fetch(domain: str) -> dict:
    calls.append(domain)
    if domain not in FAKE:
        raise ValueError(f"could not reach {domain} (test)")
    return FAKE[domain]


@pytest.fixture
async def api(stream, store, counters, enricher, redis_client):
    verifier = Verifier({"OpenAI": ["20.15.240.64/28"], "Google": ["66.249.64.0/19"]})
    chk = Checker(redis_client, verifier, fetch=fake_fetch)
    app = create_api(counters=counters, stream=stream, enricher=enricher, self_sensor=False, store=store, checker=chk)
    async with app.router.lifespan_context(app):
        ac = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")
        yield ac
        await ac.aclose()


async def test_check_routes_cache_and_popular(api):
    calls.clear()
    r = await api.get("/v1/check/robots", params={"site": "https://WWW.nyt.test/some/page"})
    assert r.status_code == 200
    j = r.json()
    assert j["domain"] == "nyt.test" and j["has_robots"] and j["blocked"] == 2 and j["cached"] is False
    assert {x["token"]: x["status"] for x in j["rows"]}["GPTBot"] == "blocked"
    assert "User-agent: GPTBot\nDisallow: /" in j["snippet_block_training"] and "robots_excerpt" in j
    r = await api.get("/v1/check/robots", params={"site": "nyt.test"})
    assert r.json()["cached"] is True and calls == ["nyt.test"]                   # second hit served from Redis

    j = (await api.get("/v1/check/robots", params={"site": "open.test"})).json()
    assert j["has_robots"] is False and j["blocked"] == 0 and j["headline"].startswith("No robots.txt")
    j = (await api.get("/v1/check/robots", params={"site": "spa.test"})).json()
    assert j["has_robots"] is False and "not a robots file" in j["headline"]      # an HTML 200 is not a robots.txt
    j = (await api.get("/v1/check/robots", params={"site": "forbid.test"})).json()
    assert j["has_robots"] is False and "HTTP 403" in j["headline"]               # 4xx = no restrictions, said plainly
    r = await api.get("/v1/check/robots", params={"site": "broken.test"})
    assert r.status_code == 502 and "HTTP 503" in r.json()["error"]               # 5xx = unavailable, not "allowed"
    j = (await api.get("/v1/check/robots", params={"site": "pypi.test"})).json()
    assert j["blocked"] == 0 and j["partial"] == 16 and j["headline"].startswith("Blocks none")

    assert (await api.get("/v1/check/robots", params={"site": "not a domain"})).status_code == 400
    assert (await api.get("/v1/check/robots", params={"site": "localhost"})).status_code == 400
    r = await api.get("/v1/check/robots", params={"site": "down.test"})
    assert r.status_code == 502 and "could not reach" in r.json()["error"]

    pop = (await api.get("/v1/check/crawlers")).json()
    assert pop["popular"][0]["domain"] == "nyt.test" and pop["popular"][0]["checks"] == 2
    assert {c["token"] for c in pop["crawlers"]} >= {"GPTBot", "ClaudeBot", "Google-Extended", "CCBot"}
    assert "/check" in (await api.get("/sitemap.xml")).text


async def test_check_rate_limit_per_visitor(api):
    for i in range(RATE_LIMIT):
        assert (await api.get("/v1/check/robots", params={"site": "nyt.test"}, headers={"cf-connecting-ip": "198.51.100.7"})).status_code == 200
    assert (await api.get("/v1/check/robots", params={"site": "nyt.test"}, headers={"cf-connecting-ip": "198.51.100.7"})).status_code == 429
    assert (await api.get("/v1/check/robots", params={"site": "nyt.test"}, headers={"cf-connecting-ip": "198.51.100.8"})).status_code == 200


async def test_ip_verifier(api):
    j = (await api.get("/v1/check/ip", params={"ip": "20.15.240.70", "bot": "gptbot"})).json()
    assert j["verdict"] == "verified" and j["bot"] == "GPTBot" and j["operator"] == "OpenAI"
    j = (await api.get("/v1/check/ip", params={"ip": "203.0.113.9", "bot": "GPTBot"})).json()
    assert j["verdict"] == "impostor"
    j = (await api.get("/v1/check/ip", params={"ip": "203.0.113.9", "bot": "Bingbot"})).json()
    assert j["verdict"] == "unknown" and j["ranges_known"] is False                # no Microsoft ranges loaded here
    assert (await api.get("/v1/check/ip", params={"ip": "nope", "bot": "GPTBot"})).status_code == 400
    r = await api.get("/v1/check/ip", params={"ip": "1.1.1.1", "bot": "FooBot"})
    assert r.status_code == 400 and "GPTBot" in r.json()["bots"]
