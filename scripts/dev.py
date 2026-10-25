"""Single-process dev/demo server: portal + API + ingest + aggregator on one
port, no Docker, no Redis, no Redpanda, no Postgres (memory stream, fakeredis,
memory store). Data lives until the process stops.

Also what the free "demo" deployment runs (render.yaml): good enough to show
the product; NOT durable. The real stack is docker-compose.yml.

    pip install -e ".[dev]"
    python scripts/dev.py
    python scripts/seed_traffic.py --url http://localhost:8000/v1/ingest --key devkey --hits 2000
    open http://localhost:8000
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import fakeredis.aioredis
import uvicorn

from botspotter.aggregator.run import Aggregator
from botspotter.api.app import create_app as create_api
from botspotter.counters import Counters
from botspotter.enrich import Enricher
from botspotter.ingest.app import create_app as create_ingest
from botspotter.store.memory import MemoryStore
from botspotter.stream.memory import MemoryStream

INGEST_PATHS = {"/v1/ingest", "/v1/beacon"}


def build():
    stream = MemoryStream.shared()
    counters = Counters(fakeredis.aioredis.FakeRedis())
    enricher = Enricher()
    ingest = create_ingest(stream=stream, enricher=enricher)
    api = create_api(counters=counters, stream=stream, enricher=enricher)
    agg = Aggregator(stream=stream, store=MemoryStore(), counters=counters)

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            async with ingest.router.lifespan_context(ingest), api.router.lifespan_context(api):
                task = asyncio.create_task(agg.run())
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        agg.stop()
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(task, 3)
                        await send({"type": "lifespan.shutdown.complete"})
                        return
        target = ingest if scope.get("path") in INGEST_PATHS else api
        await target(scope, receive, send)

    return app


app = build()

if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO)
    # PORT is set by Render / Fly / Railway; 8000 locally.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="info")
