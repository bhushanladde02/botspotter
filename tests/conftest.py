from __future__ import annotations

import pytest
import fakeredis.aioredis

from botspotter.counters import Counters
from botspotter.enrich import Enricher
from botspotter.store.memory import MemoryStore
from botspotter.stream.memory import MemoryStream


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def counters(redis_client):
    return Counters(redis_client)


@pytest.fixture
def stream():
    MemoryStream.reset()
    return MemoryStream.shared()


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def enricher():
    return Enricher()


BROWSER_HEADERS = ["host", "user-agent", "accept", "accept-language", "accept-encoding", "connection"]
BOT_HEADERS = ["host", "user-agent", "accept", "accept-encoding"]
LIB_HEADERS = ["host", "user-agent", "accept-encoding", "connection"]

GPTBOT_UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)"
CLAUDE_UA = "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)"
CHROME_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
REQUESTS_UA = "python-requests/2.32.3"
MYSTERY_UA = "WeirdNewAgent/0.9 (+https://example.org/agent)"
