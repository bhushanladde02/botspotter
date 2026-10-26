"""Claim-your-bot: verification by fingerprint challenge or .well-known file,
then identity management with the owner key."""
from __future__ import annotations

import httpx
import pytest

from botspotter.aggregator.run import Aggregator
from botspotter.api.app import create_app as create_api
from botspotter.ingest.app import create_app as create_ingest

from .conftest import CHROME_UA, GPTBOT_UA, LIB_HEADERS

# An unidentified crawler that names its own domain (lets us test the well-known method too).
MYSTERY_UA = "MysteryCrawler/0.3 (+https://example.org/agent)"
from .test_pipeline import drain, hits


@pytest.fixture
async def world(stream, store, counters, enricher):
    ingest = create_ingest(stream=stream, enricher=enricher)
    api = create_api(counters=counters, stream=stream, enricher=enricher, self_sensor=False)
    agg = Aggregator(stream=stream, store=store, counters=counters)
    async with ingest.router.lifespan_context(ingest), api.router.lifespan_context(api):
        ic = httpx.AsyncClient(transport=httpx.ASGITransport(app=ingest), base_url="http://ingest")
        ac = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://api")
        yield ic, ac, agg, store, counters, api
        await ic.aclose()
        await ac.aclose()


# Header NAMES (in order) that httpx's test client sends when we set user-agent and
# x-forwarded-for; the seeded bot must have the same ones so the fingerprint matches,
# exactly as a real bot's own request would match its own sensor hits.
CLIENT_HEADERS = ["host", "accept", "accept-encoding", "connection", "user-agent", "x-forwarded-for"]


async def seed_bot(ic, agg, ua=MYSTERY_UA, headers=CLIENT_HEADERS, ip="203.0.113.7"):
    await ic.post("/v1/ingest", json={"events": hits(ua, headers, ip, "DE", n=2)}, headers={"X-BS-Key": "devkey"})
    await drain(agg)


def bot_headers(ua=MYSTERY_UA, headers=None, ip="203.0.113.7"):
    return {"user-agent": ua, "x-forwarded-for": ip}


async def test_fetch_verification_then_rename(world):
    ic, ac, agg, store, counters, api = world
    await seed_bot(ic, agg)
    snap = (await ac.get("/v1/snapshot")).json()
    slug = snap["bots"]["top"][0]["slug"]
    old_name = snap["bots"]["top"][0]["name"]

    # 1. start a claim
    r = await ac.post("/v1/claims", json={"slug": slug, "email": "ops@example.org", "method": "fetch"})
    assert r.status_code == 201, r.text
    claim = r.json()
    token = claim["token"]
    assert claim["verify_url"].endswith(f"/verify/{token}")
    assert (await ac.get(f"/v1/claims/{token}")).json()["status"] == "pending"

    # 2. a browser hitting the verify URL does not count
    r = await ac.get(f"/verify/{token}", headers={"user-agent": CHROME_UA, "accept-language": "en", "accept": "*/*"})
    assert r.status_code == 403 and "browser" in r.json()["error"]
    # 3. a DIFFERENT bot does not count
    r = await ac.get(f"/verify/{token}", headers=bot_headers(GPTBOT_UA, None, "20.171.1.1"))
    assert r.status_code == 403 and "does not match" in r.json()["error"]
    assert (await ac.get(f"/v1/claims/{token}")).json()["attempts"] == 1
    # 4. the real bot does
    r = await ac.get(f"/verify/{token}", headers=bot_headers())
    assert r.status_code == 200 and r.json()["verified"] is True
    st = (await ac.get(f"/v1/claims/{token}")).json()
    assert st["status"] == "verified" and st["owner_key"]
    owner = st["owner_key"]

    # 5. manage: wrong key rejected, right key renames
    r = await ac.patch(f"/v1/bots/{slug}", json={"display_name": "Acme Crawler"}, headers={"X-BS-Owner": "nope"})
    assert r.status_code == 403
    r = await ac.patch(f"/v1/bots/{slug}", headers={"X-BS-Owner": owner}, json={
        "display_name": "Acme Crawler", "description": "Indexes product pages for Acme Search.",
        "homepage": "https://acme.example/bot", "operator": "Acme Inc", "category": "search"})
    assert r.status_code == 200, r.text
    prof = (await ac.get(f"/v1/bots/{slug}")).json()
    assert prof["claimed"] and prof["display_name"] == "Acme Crawler" and prof["operator"] == "Acme Inc"
    assert prof["category"] == "search" and prof["homepage"].startswith("https://acme")
    assert prof["name"] == old_name  # generated name is kept as permanent alias
    # new name resolves and is searchable; old slug still works
    assert (await ac.get("/v1/bots/acme-crawler")).json()["fp"] == prof["fp"]
    res = (await ac.get("/v1/search", params={"q": "acme"})).json()["results"]
    assert res and res[0]["display_name"] == "Acme Crawler" and res[0]["claimed"]
    assert (await ac.get(f"/v1/bots/{slug}")).status_code == 200

    # 6. second claim on a claimed bot is refused
    r = await ac.post("/v1/claims", json={"slug": slug, "email": "x@y.io", "method": "fetch"})
    assert r.status_code == 409


async def test_name_rules_and_collisions(world):
    ic, ac, agg, store, counters, api = world
    await seed_bot(ic, agg)                                   # mystery bot
    await seed_bot(ic, agg, GPTBOT_UA, CLIENT_HEADERS, "20.171.1.1")  # gptbot
    snap = (await ac.get("/v1/snapshot")).json()
    by_op = {b["operator"]: b for b in snap["bots"]["top"]}
    mystery, gpt = by_op["unknown"], by_op["OpenAI"]

    async def claim(slug, hdrs):
        tok = (await ac.post("/v1/claims", json={"slug": slug, "email": "a@b.co", "method": "fetch"})).json()["token"]
        assert (await ac.get(f"/verify/{tok}", headers=hdrs)).status_code == 200
        return (await ac.get(f"/v1/claims/{tok}")).json()["owner_key"]

    k1 = await claim(mystery["slug"], bot_headers())
    k2 = await claim(gpt["slug"], bot_headers(GPTBOT_UA, None, "20.171.1.1"))
    assert (await ac.patch(f"/v1/bots/{mystery['slug']}", json={"display_name": "Zed"}, headers={"X-BS-Owner": k1})).status_code == 200
    # collision with another bot's display name
    r = await ac.patch(f"/v1/bots/{gpt['slug']}", json={"display_name": "zed"}, headers={"X-BS-Owner": k2})
    assert r.status_code == 409
    # collision with an existing GENERATED name
    r = await ac.patch(f"/v1/bots/{gpt['slug']}", json={"display_name": mystery["name"]}, headers={"X-BS-Owner": k2})
    assert r.status_code == 409
    # reserved / invalid / bad category / bad url
    for bad in ({"display_name": "about"}, {"display_name": "x"}, {"display_name": "<script>"},
                {"category": "evil"}, {"homepage": "ftp://x"}):
        assert (await ac.patch(f"/v1/bots/{gpt['slug']}", json=bad, headers={"X-BS-Owner": k2})).status_code in (400, 409), bad
    # clearing the custom name frees it
    assert (await ac.patch(f"/v1/bots/{mystery['slug']}", json={"display_name": ""}, headers={"X-BS-Owner": k1})).status_code == 200
    assert (await ac.get("/v1/bots/zed")).status_code == 404
    assert (await ac.patch(f"/v1/bots/{gpt['slug']}", json={"display_name": "Zed"}, headers={"X-BS-Owner": k2})).status_code == 200


async def test_wellknown_verification(world, monkeypatch):
    ic, ac, agg, store, counters, api = world
    await seed_bot(ic, agg, GPTBOT_UA, CLIENT_HEADERS, "20.171.1.1")  # registry homepage: platform.openai.com
    slug = (await ac.get("/v1/snapshot")).json()["bots"]["top"][0]["slug"]
    r = await ac.post("/v1/claims", json={"slug": slug, "email": "a@b.co", "method": "wellknown"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["host"] == "platform.openai.com" and c["file_url"].endswith("/.well-known/botspotter-verify.txt") and c["content"] == f"botspotter-verify={c['token']}"
    served = {"body": "nothing here"}
    api.state.claims._fetch = lambda host: served["body"]
    assert (await ac.post(f"/v1/claims/{c['token']}/check")).status_code == 403
    served["body"] = f"# verification\nbotspotter-verify={c['token']}\n"
    assert (await ac.post(f"/v1/claims/{c['token']}/check")).json()["verified"] is True
    assert (await ac.get(f"/v1/claims/{c['token']}")).json()["status"] == "verified"
    # an unknown bot that names a domain in its UA can use wellknown against that domain
    await seed_bot(ic, agg, MYSTERY_UA)  # "+https://example.org/agent"
    myst = [b for b in (await ac.get("/v1/snapshot")).json()["bots"]["top"] if b["operator"] == "unknown"][0]
    r = await ac.post("/v1/claims", json={"slug": myst["slug"], "email": "a@b.co", "method": "wellknown"})
    assert r.status_code == 201 and r.json()["host"] == "example.org"
    # a bot with no homepage and no URL in its UA can't use wellknown
    await seed_bot(ic, agg, "python-requests/2.32", LIB_HEADERS, "45.33.1.1")
    other = [b for b in (await ac.get("/v1/snapshot")).json()["bots"]["top"] if "python-requests" in (await ac.get(f"/v1/bots/{b['slug']}")).json()["ua_family"]][0]
    r = await ac.post("/v1/claims", json={"slug": other["slug"], "email": "a@b.co", "method": "wellknown"})
    assert r.status_code == 400


async def test_claim_validation_and_rate_limit(world):
    ic, ac, agg, store, counters, api = world
    await seed_bot(ic, agg)
    slug = (await ac.get("/v1/snapshot")).json()["bots"]["top"][0]["slug"]
    assert (await ac.post("/v1/claims", json={"slug": "nobody-0000", "email": "a@b.co"})).status_code == 404
    assert (await ac.post("/v1/claims", json={"slug": slug, "email": "not-an-email"})).status_code == 400
    assert (await ac.post("/v1/claims", json={"slug": slug, "email": "a@b.co", "method": "magic"})).status_code == 400
    codes = [(await ac.post("/v1/claims", json={"slug": slug, "email": "a@b.co"}, headers={"x-forwarded-for": "198.51.100.5"})).status_code for _ in range(7)]
    assert codes.count(201) == 5 and codes[-1] == 429
    assert (await ac.get("/v1/claims/does-not-exist")).status_code == 404
    assert (await ac.get("/verify/does-not-exist", headers=bot_headers())).status_code == 404
