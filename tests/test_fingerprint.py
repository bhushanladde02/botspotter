from botspotter.fingerprint import (make_fingerprint, name_from_digest, network_prefix,
                                    normalize_user_agent, slugify_name)
from botspotter.names import ADJECTIVES, ANIMALS

from .conftest import BOT_HEADERS, GPTBOT_UA


def test_normalize_strips_versions_and_parens():
    a = normalize_user_agent("GPTBot/1.1")
    b = normalize_user_agent("GPTBot/1.2 (+https://openai.com/gptbot)")
    assert a == b == "gptbot/#"


def test_network_prefix_is_coarse_and_never_the_ip():
    assert network_prefix("20.171.207.5") == "net:20.171.0.0/16"
    assert network_prefix("20.171.1.1") == network_prefix("20.171.255.254")
    assert "2001:db8" in network_prefix("2001:db8:1234::1")
    assert network_prefix("not-an-ip") == "net:unknown"


def test_same_bot_same_name_everywhere():
    fp1 = make_fingerprint(GPTBOT_UA, BOT_HEADERS, "20.171.207.5")
    fp2 = make_fingerprint(GPTBOT_UA.replace("1.2", "1.3"), BOT_HEADERS, "20.171.9.9")
    assert fp1.digest == fp2.digest
    assert name_from_digest(fp1.digest) == name_from_digest(fp2.digest)


def test_software_change_gives_new_name_same_family():
    fp1 = make_fingerprint(GPTBOT_UA, BOT_HEADERS, "20.171.207.5")
    fp2 = make_fingerprint(GPTBOT_UA, BOT_HEADERS + ["x-new-header"], "20.171.207.5")
    assert fp1.digest != fp2.digest
    assert fp1.family_digest == fp2.family_digest


def test_proxy_headers_do_not_change_fingerprint():
    fp1 = make_fingerprint(GPTBOT_UA, BOT_HEADERS, "20.171.207.5")
    fp2 = make_fingerprint(GPTBOT_UA, BOT_HEADERS + ["cf-ray", "x-forwarded-for", "cf-connecting-ip"], "20.171.207.5")
    assert fp1.digest == fp2.digest


def test_name_format_and_slug():
    name = name_from_digest("ffffffffffffffffffffffffffffffff")
    adj, animal, num = name.split(" ")
    assert adj in ADJECTIVES and animal in ANIMALS and num.startswith("#") and len(num) == 5
    assert slugify_name("Copper Heron #0042") == "copper-heron-0042"


def test_name_space_is_wide():
    import hashlib

    names = {name_from_digest(hashlib.blake2b(str(i).encode(), digest_size=16).hexdigest()) for i in range(30000)}
    assert len(names) > 29000  # 40M name space -> almost no collisions in 30k


def test_known_bot_is_one_identity_across_networks():
    from botspotter.enrich import Enricher
    from botspotter.models import SensorHit

    en = Enricher()
    a = en.bot_event(SensorHit(ua=GPTBOT_UA, headers=BOT_HEADERS, ip="20.171.1.1"), "s")
    b = en.bot_event(SensorHit(ua=GPTBOT_UA, headers=BOT_HEADERS, ip="52.230.9.9"), "s")
    assert a.fp == b.fp and a.name == b.name
    # unknown automation keeps the network in its identity
    c = en.bot_event(SensorHit(ua="python-requests/2.32", headers=BOT_HEADERS, ip="45.33.1.1"), "s")
    d = en.bot_event(SensorHit(ua="python-requests/2.32", headers=BOT_HEADERS, ip="139.59.1.1"), "s")
    assert c.fp != d.fp and c.fam == d.fam
