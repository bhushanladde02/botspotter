"""Self-serve sensor enrollment: sign in (GitHub OAuth, mocked), prove the domain,
get a key, ingest accepts it, the map knows the site. Nothing without auth."""
import httpx
import pytest

from botspotter.aggregator.run import Aggregator
from botspotter.api.app import create_app as create_api
from botspotter.enroll import Enrollment, GitHubUser, Sessions, normalize_domain
from botspotter.ingest.app import create_app as create_ingest

from .conftest import BOT_HEADERS, GPTBOT_UA
from .test_pipeline import drain

TOKENS: dict[str, str] = {}      # domain -> token the fake "DNS"/"file" will answer with


async def fake_dns(domain: str) -> list[str]:
    return [f"botspotter-verify={TOKENS[domain]}"] if domain in TOKENS and domain.endswith(".dns") else []


async def fake_wellknown(domain: str) -> str:
    return f"# proof\nbotspotter-verify={TOKENS[domain]}\n" if domain in TOKENS and domain.endswith(".web") else ""


@pytest.fixture
async def world(stream, store, counters, enricher, redis_client):
    async def exchange(code: str) -> GitHubUser:
        return {"code-alice": GitHubUser(1001, "alice", "Alice", "https://avatars/alice"),
                "code-bob": GitHubUser(1002, "bob", "Bob", "")}[code]

    enroll = Enrollment(redis_client, enricher.geo, dns=fake_dns, wellknown=fake_wellknown)
    ingest = create_ingest(stream=stream, enricher=enricher, redis=redis_client)
    api = create_api(counters=counters, stream=stream, enricher=enricher, self_sensor=False, store=store,
                     enrollment=enroll, exchange=exchange)
    agg = Aggregator(stream=stream, store=store, counters=counters)
    async with ingest.router.lifespan_context(ingest), api.router.lifespan_context(api):
        ic = httpx.AsyncClient(transport=httpx.ASGITransport(app=ingest), base_url="http://ingest")
        ac = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://api")
        yield ic, ac, agg, counters, enroll
        await ic.aclose()
        await ac.aclose()


async def sign_in(ac: httpx.AsyncClient, code: str) -> httpx.AsyncClient:
    r = await ac.get("/auth/github")
    assert r.status_code == 302 and "github.com/login/oauth/authorize" in r.headers["location"]
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = await ac.get(f"/auth/github/callback?code={code}&state={state}")
    assert r.status_code == 302 and r.headers["location"] == "/sensors" and "bs_session=" in r.headers.get("set-cookie", "")
    return ac


def test_sessions_and_domains():
    s = Sessions("secret")
    tok = s.issue(42)
    assert s.read(tok) == 42 and s.read(tok[:-1] + "x") is None and s.read("garbage") is None and s.read(None) is None
    assert Sessions("other").read(tok) is None
    assert normalize_domain("https://WWW.Example.com/path") == "example.com"
    assert normalize_domain("sharpenscore.com") == "sharpenscore.com"
    assert normalize_domain("not a domain") is None and normalize_domain("localhost") is None


async def test_everything_requires_sign_in(world):
    ic, ac, agg, counters, enroll = world
    assert (await ac.get("/v1/me")).json()["user"] is None
    assert (await ac.get("/v1/sites/mine")).status_code == 401
    assert (await ac.post("/v1/sites", json={"domain": "example.com"})).status_code == 401
    assert (await ac.post("/v1/sites/example.com/verify")).status_code == 401
    r = await ac.get("/auth/github/callback?code=code-alice&state=forged")
    assert r.status_code == 400                                   # CSRF state must match the cookie


async def test_enroll_verify_key_ingest_and_map(world):
    ic, ac, agg, counters, enroll = world
    await sign_in(ac, "code-alice")
    assert (await ac.get("/v1/me")).json()["user"]["login"] == "alice"

    r = await ac.post("/v1/sites", json={"domain": "https://www.My-Site.web/"})
    assert r.status_code == 201
    j = r.json(); assert j["domain"] == "my-site.web" and j["status"] == "pending"
    assert j["dns"]["name"] == "_botspotter.my-site.web" and j["wellknown"]["url"].endswith("/.well-known/botspotter-verify.txt")

    r = await ac.post("/v1/sites/my-site.web/verify")             # proof not published yet
    assert r.status_code == 403 and r.json()["verified"] is False
    TOKENS["my-site.web"] = j["token"]                            # owner publishes the file
    r = await ac.post("/v1/sites/my-site.web/verify")
    assert r.status_code == 200
    key = r.json()["key"]; assert len(key) == 32 and r.json()["via"] == "wellknown"
    mine = (await ac.get("/v1/sites/mine")).json()["sites"]
    assert mine[0]["domain"] == "my-site.web" and mine[0]["status"] == "verified" and mine[0]["token"] is None

    # the fresh key is accepted by ingest without any env change, and the hits land on the right site
    r = await ic.post("/v1/ingest", json={"events": [{"ua": GPTBOT_UA, "headers": BOT_HEADERS, "ip": "20.15.241.9", "cf_country": "US", "path": "/"}]},
                      headers={"X-BS-Key": key})
    assert r.status_code == 202 and r.json()["site"] == "my-site.web"
    assert (await ic.post("/v1/ingest", json={"events": []}, headers={"X-BS-Key": "nonsense"})).status_code == 401
    await drain(agg)
    snap = (await ac.get("/v1/snapshot")).json()
    assert "my-site.web" in {x["site"] for x in snap["network"]["sites"]}
    assert any(s["site"] == "my-site.web" for s in (await ac.get("/v1/sensors")).json()["sensors"])
    assert (await ac.get("/v1/sites/mine")).json()["sites"][0]["hits"] == 1

    # rotate: old key dies within the cache window (fresh ingest cache here -> immediately for a new key lookup)
    r = await ac.post("/v1/sites/my-site.web/rotate"); new_key = r.json()["key"]
    assert new_key != key
    assert await enroll.site_for_key(key) is None and await enroll.site_for_key(new_key) == "my-site.web"

    # remove revokes
    assert (await ac.delete("/v1/sites/my-site.web")).status_code == 200
    assert await enroll.site_for_key(new_key) is None and (await ac.get("/v1/sites/mine")).json()["sites"] == []


async def test_dns_proof_and_ownership_isolation(world):
    ic, ac, agg, counters, enroll = world
    await sign_in(ac, "code-alice")
    j = (await ac.post("/v1/sites", json={"domain": "alice.dns"})).json()
    TOKENS["alice.dns"] = j["token"]
    assert (await ac.post("/v1/sites/alice.dns/verify")).json()["via"] == "dns"
    # bob cannot touch alice's domain
    bob = httpx.AsyncClient(transport=ac._transport, base_url="http://api")
    await sign_in(bob, "code-bob")
    assert (await bob.post("/v1/sites", json={"domain": "alice.dns"})).status_code == 409
    assert (await bob.post("/v1/sites/alice.dns/rotate")).status_code == 404
    assert (await bob.delete("/v1/sites/alice.dns")).status_code == 404
    await bob.aclose()
