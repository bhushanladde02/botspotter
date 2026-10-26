from botspotter.avatar import avatar_svg, PALETTE
from botspotter.names import ADJECTIVES


def test_avatar_is_deterministic_and_valid_svg():
    a = avatar_svg("a" * 32, "Copper Heron #0042")
    b = avatar_svg("a" * 32, "Copper Heron #0042")
    c = avatar_svg("b" * 32, "Copper Heron #0042")
    assert a == b and a != c
    assert a.startswith("<svg") and a.endswith("</svg>") and "#b87333" in a  # copper


def test_every_adjective_has_a_colour():
    assert set(ADJECTIVES) <= set(PALETTE)


def test_variety():
    import hashlib
    faces = {avatar_svg(hashlib.blake2b(str(i).encode(), digest_size=16).hexdigest(), "Neon Fox #0001") for i in range(2000)}
    assert len(faces) > 400


from .test_pipeline import world  # noqa: F401,E402


async def test_avatar_endpoint(world):
    from .test_pipeline import drain, hits
    from .conftest import BOT_HEADERS, GPTBOT_UA
    ic, ac, agg, store, counters = world
    await ic.post("/v1/ingest", json={"events": hits(GPTBOT_UA, BOT_HEADERS, "20.171.207.5", "US")}, headers={"X-BS-Key": "devkey"})
    await drain(agg)
    slug = (await ac.get("/v1/snapshot")).json()["bots"]["top"][0]["slug"]
    r = await ac.get(f"/avatar/{slug}.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg") and "<svg" in r.text
    assert (await ac.get("/avatar/nobody-0000.svg")).status_code == 404
