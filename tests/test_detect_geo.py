from botspotter.detect import Classifier, classify
from botspotter.geo import Geo, centroid
from botspotter.geohash import decode, encode

from .conftest import (BOT_HEADERS, BROWSER_HEADERS, CHROME_UA, CLAUDE_UA, GPTBOT_UA, LIB_HEADERS, MYSTERY_UA,
                       REQUESTS_UA)


def test_known_bots():
    c = classify(GPTBOT_UA, BOT_HEADERS)
    assert c.is_bot and c.kind == "known" and c.operator == "OpenAI" and c.category == "training"
    c = classify(CLAUDE_UA, BOT_HEADERS)
    assert c.operator == "Anthropic"


def test_longest_pattern_wins():
    c = classify("Mozilla/5.0 (compatible; Claude-SearchBot/1.0)", BOT_HEADERS)
    assert c.label == "Claude-SearchBot" and c.category == "search"


def test_http_library_is_heuristic_bot():
    c = classify(REQUESTS_UA, LIB_HEADERS)
    assert c.is_bot and c.kind == "heuristic" and c.label.startswith("ua:python-requests")


def test_browser_is_human():
    c = classify(CHROME_UA, BROWSER_HEADERS)
    assert not c.is_bot and c.kind == "human"


def test_mystery_agent_without_accept_is_bot():
    c = classify(MYSTERY_UA, ["host", "user-agent"])
    assert c.is_bot and c.label == "no-accept-header"


def test_empty_ua_is_bot():
    assert classify("", []).is_bot


def test_registry_sorted_longest_first():
    reg = Classifier().registry
    assert all(len(reg[i].pattern) >= len(reg[i + 1].pattern) for i in range(len(reg) - 1))


def test_geo_fallback_chain():
    g = Geo(mmdb_path="")  # no database
    assert not g.has_mmdb
    r = g.lookup("1.2.3.4", cf_country="DE")
    assert r.country == "DE" and r.source == "cf-header" and r.lat != 0
    r = g.lookup("1.2.3.4", cf_country="")
    assert r.country == "XX" and r.source == "none"
    assert centroid("IN")[0] > 0


def test_geohash_roundtrip():
    gh = encode(37.7749, -122.4194, 5)
    lat, lon = decode(gh)
    assert abs(lat - 37.7749) < 0.05 and abs(lon + 122.4194) < 0.05
    assert encode(37.7749, -122.4194, 3) == gh[:3]
