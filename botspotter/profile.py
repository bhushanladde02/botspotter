"""What a bot is for, and how it behaves — shown on each bot's page.

Two sources, never mixed up:
  about      — for known bots: what the OPERATOR says (data/bot_profiles.json, sourced from
               their docs) + whether we can verify it (published IP ranges, verify.SOURCES).
  behaviour  — for every bot: facts we observed ourselves (robots.txt fetches, hosting networks,
               reach, timing, how it identifies itself) and a hedged one-line "looks like" summary.
No new data is collected for this: everything comes from counters we already keep.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from .verify import SOURCES

USES_LABEL = {
    "training": "may be used to train AI models",
    "search": "used to build a search or AI-search index",
    "live answers": "fetched to answer a person's question right now",
    "link preview": "used to build a link preview when someone shares your URL",
    "monitoring": "an uptime check someone set up for this site",
    "SEO data": "sold as SEO / backlink data",
    "dataset": "added to a dataset others reuse (often for AI training)",
    "scraping": "extracted and reused or resold as data",
    "other": "used inside the operator's own products",
}
ROBOTS_LABEL = {
    "honours": "Honours robots.txt (per the operator)",
    "may ignore": "May ignore robots.txt (per the operator)",
    "not a crawler": "Not a crawler: robots.txt controls how others' crawls are used, or it is a monitor",
    "not stated": "Operator does not say whether it honours robots.txt",
}
# Heuristic labels from detect.py -> plain English. Keys are matched as prefixes of the label.
TOOL_HINTS = [
    ("ua:python-requests", "a Python script (requests library)"),
    ("ua:python-urllib", "a Python script (urllib)"),
    ("ua:aiohttp", "a Python script (aiohttp)"),
    ("ua:httpx", "a Python script (httpx)"),
    ("ua:scrapy", "the Scrapy scraping framework"),
    ("ua:go-http-client", "a program written in Go"),
    ("ua:okhttp", "a Java/Android HTTP client"),
    ("ua:java/", "a Java program"),
    ("ua:apache-httpclient", "a Java program (Apache HttpClient)"),
    ("ua:curl/", "the curl command-line tool"),
    ("ua:wget/", "the wget command-line tool"),
    ("ua:node-fetch", "a Node.js script"), ("ua:undici", "a Node.js script"), ("ua:axios", "a Node.js script (axios)"),
    ("ua:headlesschrome", "a headless Chrome browser (browser automation)"),
    ("ua:puppeteer", "Puppeteer browser automation"), ("ua:playwright", "Playwright browser automation"),
    ("ua:selenium", "Selenium browser automation"), ("ua:phantomjs", "PhantomJS browser automation"),
    ("ua:guzzle", "a PHP program (Guzzle)"), ("ua:libwww", "a Perl script"), ("ua:http.rb", "a Ruby program"),
    ("ua:colly", "a Go scraper (Colly)"), ("ua:resty", "a Go program (Resty)"),
    ("ua:crawler", "a self-described crawler"), ("ua:spider", "a self-described spider"),
    ("ua:bot", "a self-described bot"), ("ua:fetch/", "a generic fetch client"),
    ("empty-user-agent", "a client that sends no User-Agent at all"),
    ("no-accept-header", "a client without the headers every real browser sends"),
]


@lru_cache(maxsize=1)
def _profiles() -> dict[str, dict]:
    raw = resources.files("botspotter.data").joinpath("bot_profiles.json").read_text()
    return {k.lower(): v for k, v in json.loads(raw)["bots"].items()}


def about(kind: str, label: str, operator: str) -> dict | None:
    """The operator's own description of a known bot (None for unidentified ones)."""
    if kind != "known" or not label:
        return None
    p = _profiles().get(label.lower())
    if not p:
        return None
    return {
        "purpose": p["purpose"], "uses": p["uses"], "uses_text": USES_LABEL.get(p["uses"], p["uses"]),
        "robots": p["robots"], "robots_text": ROBOTS_LABEL.get(p["robots"], p["robots"]),
        "token": p.get("token", label), "note": p.get("note", ""),
        "verifiable": operator in SOURCES,
    }


def tool_hint(label: str) -> str:
    l = (label or "").lower()
    for prefix, text in TOOL_HINTS:
        if l.startswith(prefix):
            return text
    return ""


def blurb(kind: str, label: str, operator: str = "") -> str:
    """One short line for list hovers: the operator's purpose, or what tool it looks like."""
    a = about(kind, label, operator)
    if a:
        return a["purpose"]
    if kind == "known":                      # registry-sync added it; nobody has written its description yet
        return f"A known {operator} bot (no description yet)." if operator and operator != "unknown" else "A known bot (no description yet)."
    t = tool_hint(label)
    return f"Unidentified — looks like {t}." if t else "Unidentified automation."


def _browser_like(ua_family: str) -> bool:
    u = (ua_family or "").lower()
    return u.startswith("mozilla/") and any(x in u for x in ("chrome/", "safari/", "firefox/", "gecko)"))


def behaviour(*, kind: str, label: str, ua_family: str, robots_hits: int, hits_tracked: int,
              networks: list[tuple[str, int]], verdicts: dict[str, int], n_sites: int,
              n_countries: int, hourly14: list[int]) -> dict:
    """Observed behaviour + a hedged summary. hourly14: 14*24 hourly hit counts, oldest first.
    hits_tracked: hits since robots.txt fetches started being counted for every bot — the only
    hits a "never reads robots.txt" judgement may rest on."""
    facts: list[dict] = []
    total14 = sum(hourly14)
    active = sum(1 for x in hourly14 if x)
    peak = max(hourly14) if hourly14 else 0
    if total14 == 0:
        rhythm, rhythm_text = "quiet", "no visits in the last 14 days"
    elif active <= 3 or (total14 >= 20 and peak / total14 >= 0.5):
        rhythm, rhythm_text = "bursty", f"arrives in bursts ({peak} hits in its busiest hour, active {active} of 336 hours)"
    elif active >= 120:
        rhythm, rhythm_text = "steady", f"visits steadily (active {active} of the last 336 hours)"
    else:
        rhythm, rhythm_text = "occasional", f"visits now and then (active {active} of the last 336 hours)"
    facts.append({"k": "Rhythm", "v": rhythm_text})

    # robots.txt: judged only on hits since we started counting robots fetches.
    if robots_hits:
        robots, robots_text = "reads", f"fetched robots.txt {robots_hits}×"
    elif hits_tracked >= 20:
        robots, robots_text = "never", f"has not fetched robots.txt in its last {hits_tracked} visits"
    else:
        robots, robots_text = "unknown", "too few visits yet to say whether it reads robots.txt"
    facts.append({"k": "robots.txt", "v": robots_text})

    if networks:
        top = ", ".join(f"{n} ({c})" for n, c in networks[:3])
        facts.append({"k": "Runs on", "v": top})
    facts.append({"k": "Reach", "v": f"{n_sites} site{'s' if n_sites != 1 else ''}, {n_countries} countr{'ies' if n_countries != 1 else 'y'}"})

    tool = tool_hint(label) if kind != "known" else ""
    disguised = kind != "known" and _browser_like(ua_family)
    if tool:
        facts.append({"k": "Identifies as", "v": tool})
    elif disguised:
        facts.append({"k": "Identifies as", "v": "an ordinary web browser, but behaves like automation"})
    imp, ver = verdicts.get("impostor", 0), verdicts.get("verified", 0)
    if imp or ver:
        facts.append({"k": "Identity check", "v": f"{ver} hits from the operator's published IPs, {imp} from elsewhere"})

    summary = ""
    if kind != "known":
        who = tool or ("a script posing as a browser" if disguised else "an unidentified client")
        where = ""
        if networks:
            n = networks[0][0]
            where = f" on {n}" if n not in ("", "unknown") else ""
        parts = []
        if robots == "never":
            parts.append("ignores robots.txt")
        elif robots == "reads":
            parts.append("reads robots.txt")
        if rhythm in ("bursty", "steady"):
            parts.append("comes in bursts" if rhythm == "bursty" else "crawls steadily")
        if n_sites > 1:
            parts.append(f"has hit {n_sites} sites")
        summary = f"Looks like {who}{where}" + (f" that {', '.join(parts[:-1]) + (' and ' if len(parts) > 1 else '') + parts[-1]}" if parts else "") + "."
    return {"rhythm": rhythm, "robots": robots, "facts": facts, "summary": summary}
