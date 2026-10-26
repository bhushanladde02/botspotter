"""Owner-only audience ledger: per-visitor rows without any IP on disk."""
from botspotter.audience import build_ledger
from botspotter.enrich import first_language, ua_client
from botspotter.models import PortalVisit

from .conftest import CHROME_UA
from .test_pipeline import drain, world  # noqa: F401

IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
EDGE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"


def test_ua_client_is_coarse():
    assert ua_client(CHROME_UA) == ("Chrome", "macOS", "desktop")
    assert ua_client(IPHONE_UA) == ("Safari", "iOS", "mobile")
    assert ua_client(EDGE_UA) == ("Edge", "Windows", "desktop")
    assert ua_client("") == ("other", "other", "desktop")
    assert first_language("en-US,en;q=0.9,hi;q=0.8") == "en-US" and first_language("*") == ""


def test_ledger_sessions_and_returning():
    t0 = 1_800_000_000.0
    mk = lambda ts, vs, **kw: PortalVisit(**{"ts": ts, "visitor": "d" + vs, "vs": vs, "country": "US", **kw})  # noqa: E731
    visits = [
        mk(t0, "aaa", page="/", ref="linkedin.com", city="Folsom", region="CA", browser="Chrome", os="macOS", device="desktop", lang="en-US"),
        mk(t0 + 60, "aaa", page="/about"),
        mk(t0 + 3 * 3600, "aaa", page="/"),                  # new session, same day
        mk(t0 + 2 * 86400, "aaa", page="/stats"),             # another day -> returning
        mk(t0 + 100, "bbb", country="IN", page="/", ref="direct", device="mobile"),
    ]
    visits.sort(key=lambda v: v.ts, reverse=True)
    led = build_ledger(visits, 30)
    s = led["summary"]
    assert s["visitors"] == 2 and s["returning_visitors"] == 1 and s["sessions"] == 4 and s["views"] == 5
    a = next(r for r in led["visitors"] if r["id"] == "aaa")
    assert a["sessions"] == 3 and a["days_active"] == 2 and a["returning"] and a["first_ref"] == "linkedin.com"
    assert a["city"] == "Folsom, CA" and a["browser"] == "Chrome"
    assert {d["date"] for d in s["daily"]} and s["sources"][0][0] in ("linkedin.com", "direct")


async def test_admin_routes_need_key_and_store_no_ip(world):
    ic, ac, agg, store, counters = world
    hdr = {"user-agent": CHROME_UA, "x-forwarded-for": "198.51.100.7", "cf-ipcountry": "US", "cf-ipcity": "Folsom",
           "cf-region-code": "CA", "accept-language": "en-US,en;q=0.9"}
    await ic.post("/v1/beacon", json={"page": "/", "ref": "https://www.linkedin.com/feed/"}, headers=hdr)
    await ic.post("/v1/beacon", json={"page": "/about"}, headers=hdr)
    await drain(agg)
    assert (await ac.get("/v1/admin/visitors")).status_code == 404            # no key -> not found, not 401
    assert (await ac.get("/v1/admin/visitors", headers={"X-BS-Admin": "wrong"})).status_code == 404
    r = await ac.get("/v1/admin/visitors", headers={"X-BS-Admin": "adminkey"})
    assert r.status_code == 200
    led = r.json()
    assert led["summary"]["visitors"] == 1 and led["summary"]["views"] == 2
    v = led["visitors"][0]
    assert v["city"] == "Folsom, CA" and v["browser"] == "Chrome" and v["os"] == "macOS" and v["lang"] == "en-US"
    assert v["first_ref"] == "linkedin.com" and v["pages"][0][0] in ("/", "/about")
    csv_ = (await ac.get("/v1/admin/visits.csv?key=adminkey")).text
    assert "198.51.100.7" not in csv_ and "Folsom" in csv_ and csv_.count("\n") == 3
    # nothing identifying in the store either
    assert all("198.51.100.7" not in v.model_dump_json() for v in store.visits)
    assert "Disallow: /admin" in (await ac.get("/robots.txt")).text


async def test_admin_disabled_without_key(stream, store, counters, enricher):
    import httpx
    from botspotter.api.app import create_app
    api = create_app(counters=counters, stream=stream, enricher=enricher, self_sensor=False, store=store, admin_key="")
    async with api.router.lifespan_context(api):
        ac = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://api")
        assert (await ac.get("/v1/admin/visitors", headers={"X-BS-Admin": ""})).status_code == 404
        await ac.aclose()


async def test_client_error_reports_are_stored_and_owner_readable(world):
    ic, ac, agg, store, counters = world
    r = await ac.post("/v1/client-error", content=b'{"m":"TypeError: x is undefined","s":"index.html:1:2","p":"/"}',
                      headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Safari/604.1"})
    assert r.status_code == 204
    assert (await ac.get("/v1/admin/client-errors")).status_code == 404
    rows = (await ac.get("/v1/admin/client-errors", headers={"X-BS-Admin": "adminkey"})).json()["errors"]
    assert rows and rows[0]["m"].startswith("TypeError") and "iPhone" in rows[0]["ua"]
