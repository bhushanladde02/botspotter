"""Bot purpose (operator's own words) and observed behaviour on each bot's page."""
import json
from importlib import resources

import httpx
import pytest

from botspotter.api.app import create_app as create_api
from botspotter.aggregator.run import Aggregator
from botspotter.detect import Classifier
from botspotter.ingest.app import create_app as create_ingest
from botspotter.profile import ROBOTS_LABEL, USES_LABEL, about, behaviour, blurb

from .conftest import BOT_HEADERS, GPTBOT_UA
from .test_pipeline import drain, hits


def test_every_registry_bot_has_a_valid_profile():
    reg = json.loads(resources.files("botspotter.data").joinpath("known_bots.json").read_text())["bots"]
    prof = json.loads(resources.files("botspotter.data").joinpath("bot_profiles.json").read_text())["bots"]
    assert set(prof) <= {b["pattern"] for b in reg}                      # no stray descriptions
    # Registry-sync PRs add bots daily, so a missing description must not fail CI; the big ones must exist.
    assert {"GPTBot", "ClaudeBot", "Googlebot", "PerplexityBot", "bingbot", "CCBot", "Bytespider"} <= set(prof)
    for k, p in prof.items():
        assert p["purpose"].endswith(".") and len(p["purpose"]) < 160, k
        assert p["uses"] in USES_LABEL and p["robots"] in ROBOTS_LABEL, k


@pytest.mark.parametrize("ua,pattern,operator", [
    ("facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)", "facebookexternalhit", "Meta"),
    ("TelegramBot (like TwitterBot)", "TelegramBot", "Telegram"),                 # longest pattern wins
    ("Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)", "Slackbot", "Slack"),
    ("Mozilla/5.0 (compatible; Google-InspectionTool/1.0;)", "Google-InspectionTool", "Google"),
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "Googlebot", "Google"),
    ("Mozilla/5.0+(compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)", "UptimeRobot", "UptimeRobot"),
])
def test_common_non_ai_agents_are_recognised(ua, pattern, operator):
    c = Classifier().classify(ua, ["host", "user-agent"])
    assert (c.kind, c.label, c.operator) == ("known", pattern, operator)


def test_about_and_blurbs():
    g = about("known", "GPTBot", "OpenAI")
    assert g["uses"] == "training" and g["robots"] == "honours" and g["verifiable"] is True
    f = about("known", "facebookexternalhit", "Meta")
    assert f["verifiable"] is False and f["robots"] == "may ignore" and "Meta" in f["note"]
    assert about("heuristic", "ua:python-requests", "unknown") is None
    assert blurb("heuristic", "ua:python-requests") == "Unidentified — looks like a Python script (requests library)."
    assert blurb("heuristic", "something-odd") == "Unidentified automation."
    assert blurb("known", "NewBotFromSync", "Acme") == "A known Acme bot (no description yet)."
    assert blurb("known", "ChatGPT-User", "OpenAI").startswith("Fetches a page when a ChatGPT user")


def h14(**at):
    """336 hourly buckets (oldest first) with hits at given offsets from the end."""
    out = [0] * 336
    for k, v in at.items():
        out[-1 - int(k[1:])] = v
    return out


def test_behaviour_rhythm_robots_and_summary():
    burst = behaviour(kind="heuristic", label="ua:python-requests", ua_family="python-requests/#", robots_hits=0,
                      hits_tracked=60, networks=[("Hetzner", 60)], verdicts={}, n_sites=3, n_countries=1,
                      hourly14=h14(h2=55, h40=5))
    assert burst["rhythm"] == "bursty" and burst["robots"] == "never"
    assert burst["summary"] == "Looks like a Python script (requests library) on Hetzner that ignores robots.txt, comes in bursts and has hit 3 sites."

    steady = behaviour(kind="heuristic", label="no-accept-header", ua_family="mozilla/# (windows nt #) chrome/# safari/#",
                       robots_hits=4, hits_tracked=300, networks=[], verdicts={}, n_sites=1, n_countries=2,
                       hourly14=[1] * 336)
    assert steady["rhythm"] == "steady" and steady["robots"] == "reads"
    assert steady["summary"].startswith("Looks like a client without the headers every real browser sends that reads robots.txt")

    disguised = behaviour(kind="heuristic", label="x", ua_family="mozilla/# (macintosh) applewebkit/# chrome/# safari/#",
                          robots_hits=0, hits_tracked=5, networks=[("AWS", 5)], verdicts={}, n_sites=1, n_countries=1,
                          hourly14=h14(h1=5))
    assert disguised["robots"] == "unknown"                                # too few tracked hits to judge
    assert disguised["summary"] == "Looks like a script posing as a browser on AWS that comes in bursts."
    assert any(f["k"] == "Identifies as" and "ordinary web browser" in f["v"] for f in disguised["facts"])

    known = behaviour(kind="known", label="GPTBot", ua_family="gptbot/#", robots_hits=1, hits_tracked=40,
                      networks=[("Azure", 40)], verdicts={"verified": 38, "impostor": 2}, n_sites=2, n_countries=1,
                      hourly14=h14(h3=20, h30=20))
    assert known["summary"] == ""                                          # known bots: the operator's words speak
    assert {"k": "Identity check", "v": "38 hits from the operator's published IPs, 2 from elsewhere"} in known["facts"]


@pytest.fixture
async def api(stream, store, counters, enricher):
    ingest = create_ingest(stream=stream, enricher=enricher)
    api = create_api(counters=counters, stream=stream, enricher=enricher, self_sensor=False, store=store)
    agg = Aggregator(stream=stream, store=store, counters=counters)
    async with ingest.router.lifespan_context(ingest), api.router.lifespan_context(api):
        ic = httpx.AsyncClient(transport=httpx.ASGITransport(app=ingest), base_url="http://i")
        ac = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://a")
        yield ic, ac, agg, counters
        await ic.aclose(); await ac.aclose()


async def test_bot_page_carries_about_and_behaviour(api):
    ic, ac, agg, counters = api
    py = "python-requests/2.32.3"
    batch = hits(py, ["host", "user-agent", "accept-encoding", "accept", "connection"], "203.0.113.7", "DE", n=24) \
        + hits(py, ["host", "user-agent", "accept-encoding", "accept", "connection"], "203.0.113.7", "DE", path="/robots.txt") \
        + hits(GPTBOT_UA, BOT_HEADERS, "20.171.207.5", "US", n=3)
    assert (await ic.post("/v1/ingest", json={"events": batch}, headers={"X-BS-Key": "devkey"})).status_code == 202
    await drain(agg)
    snap = (await ac.get("/v1/snapshot")).json()
    by_kind = {b["kind"]: b for b in snap["bots"]["top"]}
    assert by_kind["heuristic"]["blurb"] == "Unidentified — looks like a Python script (requests library)."
    assert by_kind["known"]["blurb"].startswith("Collects public pages that may be used to train OpenAI")

    py_bot = (await ac.get(f"/v1/bots/{by_kind['heuristic']['slug']}")).json()
    assert py_bot["about"] is None
    b = py_bot["behaviour"]
    assert b["robots"] == "reads" and {"k": "robots.txt", "v": "fetched robots.txt 1×"} in b["facts"]
    assert b["summary"].startswith("Looks like a Python script (requests library)")

    gpt = (await ac.get(f"/v1/bots/{by_kind['known']['slug']}")).json()
    assert gpt["about"]["token"] == "GPTBot" and gpt["about"]["verifiable"] is True
    assert gpt["behaviour"]["summary"] == ""


async def test_recognised_later_upgrades_identity_but_never_a_claimed_bot(counters, stream, store, enricher):
    from botspotter.models import BotEvent

    def ev(fp, kind, label, op, cat):
        return BotEvent(ts=1.8e9, site="s", fp=fp, fam=fp, name=f"Bot {fp}", ua_family="slackbot-linkexpanding #",
                        kind=kind, operator=op, category=cat, label=label, country="US", region="", lat=0, lon=0, gh="9q8y")

    await counters.record_bot_events([ev("aa", "heuristic", "ua:bot", "unknown", "unknown"),
                                      ev("bb", "heuristic", "ua:bot", "unknown", "unknown")])
    await counters.r.hset("bs:bot:bb", "claimed_at", 1.8e9)             # bb's operator claimed it
    await counters.record_bot_events([ev("aa", "known", "Slackbot", "Slack", "other"),
                                      ev("bb", "known", "Slackbot", "Slack", "other")])
    assert await counters.r.hmget("bs:bot:aa", "kind", "operator", "label") == [b"known", b"Slack", b"Slackbot"]
    assert await counters.r.hmget("bs:bot:bb", "kind", "operator") == [b"heuristic", b"unknown"]
