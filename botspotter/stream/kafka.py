from __future__ import annotations

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .base import Message, Stream


class KafkaStream(Stream):
    """Redpanda / Kafka backend via aiokafka. Producer and consumer are
    created lazily so the ingest service never opens a consumer and the
    aggregator never opens a producer."""

    def __init__(self, bootstrap: str):
        self.bootstrap = bootstrap
        self._producer: AIOKafkaProducer | None = None
        self._consumer: AIOKafkaConsumer | None = None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()
            self._producer = None
        if self._consumer:
            await self._consumer.stop()
            self._consumer = None

    async def publish(self, topic: str, key: bytes, value: bytes) -> None:
        if self._producer is None:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap,
                linger_ms=20,  # batch for ~20ms -> far fewer round trips
                # No compression: events are ~300 bytes and the broker is on the same
                # host. (lz4/zstd need the `cramjam` wheel; its absence took prod down
                # with "Compression library for lz4 not found" on 2026-10-25.)
                compression_type=None,
                acks=1,
            )
            await self._producer.start()
        await self._producer.send(topic, value=value, key=key)

    async def poll(self, topics: list[str], group: str, max_records: int, timeout_s: float) -> list[Message]:
        if self._consumer is None:
            self._consumer = AIOKafkaConsumer(
                *topics,
                bootstrap_servers=self.bootstrap,
                group_id=group,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                max_poll_records=max_records,
            )
            await self._consumer.start()
        batches = await self._consumer.getmany(timeout_ms=int(timeout_s * 1000), max_records=max_records)
        out: list[Message] = []
        for _tp, records in batches.items():
            for r in records:
                out.append(Message(r.topic, r.key or b"", r.value))
        return out

    async def commit(self) -> None:
        if self._consumer is not None:
            await self._consumer.commit()
