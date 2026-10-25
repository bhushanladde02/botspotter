"""Fire realistic fake bot traffic at a running ingest endpoint so the map
and counters light up before real sensors exist.

    python scripts/seed_traffic.py --url http://localhost/v1/ingest --key devkey --sites 5 --hits 2000

Also used as the load test with --rps (sustained requests/second).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
import ssl
import urllib.request

# python.org macOS builds ship without root CAs; use certifi's bundle if present.
try:
    import certifi

    _ctx = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover
    _ctx = ssl.create_default_context()

UAS = [
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)", "20.171.{a}.{b}", "US"),
    ("Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", "160.79.{a}.{b}", "US"),
    ("Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", "44.221.{a}.{b}", "US"),
    ("Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider; spider-feedback@bytedance.com)", "110.249.{a}.{b}", "CN"),
    ("CCBot/2.0 (https://commoncrawl.org/faq/)", "3.{a}.{b}.7", "US"),
    ("meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)", "57.141.{a}.{b}", "IE"),
    ("Mozilla/5.0 (compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)", "52.70.{a}.{b}", "US"),
    ("Mozilla/5.0 (compatible; Google-Extended)", "66.249.{a}.{b}", "US"),
    ("python-requests/2.32.3", "45.33.{a}.{b}", "DE"),
    ("Go-http-client/2.0", "139.59.{a}.{b}", "SG"),
    ("Scrapy/2.11 (+https://scrapy.org)", "185.199.{a}.{b}", "NL"),
    ("MysteryCrawler/0.3 (+https://example.net/bot)", "103.21.{a}.{b}", "IN"),
    ("NovaAgent/1.0", "51.15.{a}.{b}", "FR"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", "73.{a}.{b}.9", "US"),  # human, filtered
]
HDRS_BOT = ["host", "user-agent", "accept", "accept-encoding"]
HDRS_LIB = ["host", "user-agent", "accept-encoding", "connection"]
HDRS_HUMAN = ["host", "user-agent", "accept", "accept-language", "accept-encoding", "sec-fetch-mode"]
COUNTRY_SPREAD = ["US", "US", "US", "DE", "IN", "SG", "GB", "FR", "JP", "BR", "NL", "IE", "CN", "AU", "CA", "KR"]


def hit(country_override=None):
    ua, ipt, cc = random.choice(UAS)
    ip = ipt.format(a=random.randint(0, 255), b=random.randint(1, 254))
    if "Mozilla" in ua and "compatible" not in ua and "Bytespider" not in ua:
        hdrs = HDRS_HUMAN
    elif "/" in ua.split(" ")[0] and "Mozilla" not in ua:
        hdrs = HDRS_LIB
    else:
        hdrs = HDRS_BOT
    if random.random() < 0.3:
        cc = random.choice(COUNTRY_SPREAD)
    return {"ts": time.time(), "ua": ua, "headers": hdrs, "ip": ip, "cf_country": country_override or cc,
            "path": "/robots.txt" if random.random() < 0.1 else "/"}


def post(url, key, events):
    req = urllib.request.Request(url, data=json.dumps({"events": events}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-BS-Key": key})
    with urllib.request.urlopen(req, timeout=10, context=_ctx) as r:
        return json.loads(r.read())


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost/v1/ingest")
    ap.add_argument("--key", default="devkey", help="comma-separated keys to rotate across (one per site)")
    ap.add_argument("--hits", type=int, default=1000)
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--rps", type=float, default=0, help="sustain N batches/sec for --seconds (load test)")
    ap.add_argument("--seconds", type=int, default=30)
    a = ap.parse_args()
    keys = a.key.split(",")
    loop = asyncio.get_running_loop()

    if a.rps:
        sent = errors = 0
        t_end = time.time() + a.seconds
        lat = []
        while time.time() < t_end:
            t0 = time.perf_counter()
            tasks = [loop.run_in_executor(None, post, a.url, random.choice(keys), [hit() for _ in range(a.batch)])
                     for _ in range(int(a.rps))]
            for r in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(r, Exception):
                    errors += 1
                else:
                    sent += r["accepted"]
            lat.append(time.perf_counter() - t0)
            await asyncio.sleep(max(0, 1 - lat[-1]))
        print(f"load: {sent} hits, {errors} errors, p50 {sorted(lat)[len(lat)//2]*1000:.0f}ms per wave of {int(a.rps)} batches")
        return

    total = bots = 0
    for i in range(0, a.hits, a.batch):
        r = post(a.url, keys[i // a.batch % len(keys)], [hit() for _ in range(min(a.batch, a.hits - i))])
        total += r["accepted"]
        bots += r["bots"]
    print(f"seeded {total} hits ({bots} bots) via {a.url}")


if __name__ == "__main__":
    asyncio.run(main())
