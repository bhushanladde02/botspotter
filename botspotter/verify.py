"""Impostor detection: is a bot that *claims* to be GPTBot really coming from
OpenAI? Major operators publish the IP ranges their crawlers use; we check the
request IP against them in-process (the IP is then discarded as always).

verdict:  "verified"  operator publishes ranges and the IP is inside one
          "impostor"  operator publishes ranges and the IP is NOT inside any
          ""          operator publishes nothing we know of (cannot say)

Ranges live in crawler_ranges.json: {"OpenAI": ["20.15.240.64/28", ...], ...},
built by jobs/crawler_ranges.py from the official URLs (docs/runbook.rst) and
refreshed on the VM weekly. The packaged copy may be empty: then every verdict
is "" until the first refresh — never a false "impostor".
"""
from __future__ import annotations

import ipaddress
import logging

from .runtime import read_json

log = logging.getLogger("botspotter.verify")

# Official sources (the refresh job fetches these; kept here so the docs and the job agree).
SOURCES: dict[str, list[str]] = {
    "OpenAI": ["https://openai.com/gptbot.json", "https://openai.com/searchbot.json", "https://openai.com/chatgpt-user.json"],
    "Google": ["https://developers.google.com/static/search/apis/ipranges/googlebot.json",
               "https://developers.google.com/static/search/apis/ipranges/special-crawlers.json",
               "https://developers.google.com/static/search/apis/ipranges/user-triggered-fetchers-google.json"],
    "Microsoft": ["https://www.bing.com/toolbox/bingbot.json"],
    "Apple": ["https://search.developer.apple.com/applebot.json"],
    "Perplexity": ["https://www.perplexity.com/perplexitybot.json", "https://www.perplexity.com/perplexity-user.json"],
    "DuckDuckGo": ["https://duckduckgo.com/duckduckbot.json"],
    "Common Crawl": ["https://commoncrawl.org/ccbot.json"],
}


class Verifier:
    def __init__(self, ranges: dict[str, list[str]] | None = None):
        raw = ranges if ranges is not None else read_json("crawler_ranges.json", {})
        self.nets: dict[str, list[ipaddress._BaseNetwork]] = {}
        for operator, cidrs in (raw.get("ranges", raw) if isinstance(raw, dict) else {}).items():
            if operator.startswith("_"):
                continue
            nets = []
            for c in cidrs:
                try:
                    nets.append(ipaddress.ip_network(c, strict=False))
                except ValueError:
                    continue
            if nets:
                self.nets[operator] = nets
        self.updated = raw.get("_updated", "") if isinstance(raw, dict) else ""

    @property
    def operators(self) -> list[str]:
        return sorted(self.nets)

    def verdict(self, operator: str, ip: str) -> str:
        nets = self.nets.get(operator)
        if not nets or not ip:
            return ""
        try:
            addr = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            return ""
        return "verified" if any(addr in n for n in nets) else "impostor"
