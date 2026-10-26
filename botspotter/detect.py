"""Bot classification.

Three tiers, in order:
  1. Known AI bots (registry in data/known_bots.json, synced by jobs/registry_sync.py)
  2. Generic automation heuristics (HTTP libraries, headless browsers, no Accept header…)
  3. Human (default)

Only tiers 1 and 2 are counted on the bot map. Tier 3 feeds the human
visitor counter when the request came from the portal itself.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources

CATEGORIES = {"training", "search", "agent", "scraper", "seo", "other", "unknown"}


@dataclass(frozen=True)
class Classification:
    is_bot: bool
    kind: str  # "known" | "heuristic" | "human"
    operator: str  # "OpenAI", "unknown", …
    category: str  # one of CATEGORIES or "human"
    label: str  # registry pattern or heuristic reason
    homepage: str = ""  # registry homepage for known bots


@dataclass(frozen=True)
class KnownBot:
    pattern: str
    operator: str
    category: str
    homepage: str = ""


_LIB_RE = re.compile(
    r"(python-requests|python-urllib|aiohttp|httpx|go-http-client|okhttp|java/|libwww|"
    r"curl/|wget/|node-fetch|undici|axios|scrapy|headlesschrome|phantomjs|puppeteer|"
    r"playwright|selenium|crawler|spider|bot/|bot;|\bbot\b|fetch/|http\.rb|guzzle|"
    r"colly|resty|apache-httpclient)",
    re.IGNORECASE,
)


def _load_registry() -> list[KnownBot]:
    raw = resources.files("botspotter.data").joinpath("known_bots.json").read_text()
    data = json.loads(raw)
    bots = [KnownBot(b["pattern"], b["operator"], b["category"], b.get("homepage", "")) for b in data["bots"]]
    # Longest pattern first so "Claude-SearchBot" wins over "ClaudeBot"-style prefixes.
    bots.sort(key=lambda b: len(b.pattern), reverse=True)
    return bots


class Classifier:
    def __init__(self, registry: list[KnownBot] | None = None):
        self.registry = registry if registry is not None else _load_registry()
        self._lower = [(b.pattern.lower(), b) for b in self.registry]

    def classify(self, user_agent: str, header_names: list[str]) -> Classification:
        ua = (user_agent or "").strip()
        ua_l = ua.lower()
        for pat, bot in self._lower:
            if pat in ua_l:
                return Classification(True, "known", bot.operator, bot.category, bot.pattern, bot.homepage)

        hn = {h.lower() for h in header_names}
        if not ua:
            return Classification(True, "heuristic", "unknown", "unknown", "empty-user-agent")
        m = _LIB_RE.search(ua)
        if m:
            return Classification(True, "heuristic", "unknown", "unknown", f"ua:{m.group(1).lower()}")
        # Browsers always send Accept-Language; most HTTP libs don't.
        looks_browser = ("mozilla/" in ua_l) and ("accept-language" in hn)
        if not looks_browser and "accept" not in hn:
            return Classification(True, "heuristic", "unknown", "unknown", "no-accept-header")
        return Classification(False, "human", "-", "human", "-")


_default: Classifier | None = None


def classifier() -> Classifier:
    global _default
    if _default is None:
        _default = Classifier()
    return _default


def classify(user_agent: str, header_names: list[str]) -> Classification:
    return classifier().classify(user_agent, header_names)
