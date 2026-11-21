"""jobs/crawler_ranges.py: parses the vendors' formats, merges on partial failure, and lists
DuckAssistBot's own file (before 2026-11-21 its real hits could be flagged as impostors)."""
import importlib.util
import json
from pathlib import Path

from botspotter.verify import SOURCES, Verifier

spec = importlib.util.spec_from_file_location("crawler_ranges", Path(__file__).resolve().parents[1] / "jobs" / "crawler_ranges.py")
job = importlib.util.module_from_spec(spec); spec.loader.exec_module(job)


def test_parse_accepts_vendor_shapes():
    assert job.parse({"prefixes": [{"ipv4Prefix": "1.2.3.0/24"}, {"ipv6Prefix": "2001:db8::/32"}]}) == ["1.2.3.0/24", "2001:db8::/32"]
    assert job.parse(["5.6.7.0/24", " 8.8.8.8/32 "]) == ["5.6.7.0/24", "8.8.8.8/32"]
    assert job.parse({"prefixes": [{"ip_prefix": "9.9.9.0/24"}, {"cidr": "10.0.0.0/8"}, {}]}) == ["9.9.9.0/24", "10.0.0.0/8"]
    assert job.parse({"unexpected": 1}) == [] and job.parse(None) == []


def test_duckassistbot_list_is_a_source():
    assert "https://duckduckgo.com/duckassistbot.json" in SOURCES["DuckDuckGo"]


def test_partial_failure_merges_instead_of_freezing(tmp_path, monkeypatch):
    out = tmp_path / "crawler_ranges.json"
    out.write_text(json.dumps({"ranges": {"DuckDuckGo": ["20.191.45.212/32"], "OpenAI": ["1.1.1.0/24"]}}))
    served = {
        "https://duckduckgo.com/duckduckbot.json": ["20.191.45.212/32", "40.88.21.235/32"],
        "https://duckduckgo.com/duckassistbot.json": None,               # new list unreachable
    }

    def fake_fetch(url):
        if url in served:
            if served[url] is None:
                raise OSError("timeout")
            return served[url]
        return ["3.3.3.0/24"]                                            # every other vendor fine

    monkeypatch.setattr(job, "fetch", fake_fetch)
    assert job.main(str(out)) == 0
    r = json.loads(out.read_text())["ranges"]
    assert r["DuckDuckGo"] == ["20.191.45.212/32", "40.88.21.235/32"]    # new range added despite the failure
    assert r["OpenAI"] == ["3.3.3.0/24"]                                 # fully fetched: replaced, not merged

    served["https://duckduckgo.com/duckassistbot.json"] = ["52.142.24.149/32"]
    job.main(str(out))
    r = json.loads(out.read_text())["ranges"]
    assert r["DuckDuckGo"] == ["20.191.45.212/32", "40.88.21.235/32", "52.142.24.149/32"]
    v = Verifier(json.loads(out.read_text()))
    assert v.verdict("DuckDuckGo", "52.142.24.149") == "verified"       # a DuckAssistBot IP is no longer an impostor
