"""validated readings -> rules engine -> Postgres and telemetry.alerts (real Kafka and Postgres)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy import text

from analysis.config import Settings
from analysis.db import make_engine, make_sessions
from analysis.migrate import run_migrations
from analysis.serialization import format_ts
from analysis.store import Store
from analysis.worker import Worker

pytestmark = pytest.mark.integration


def reading_bytes(value: float, ts: datetime, metric: str = "temperature") -> bytes:
    return json.dumps(
        {
            "id": str(uuid.uuid4()),
            "deviceId": "sensor-001",
            "metric": metric,
            "value": value,
            "unit": "celsius",
            "timestamp": format_ts(ts),
        }
    ).encode()


async def collect_alerts(consumer, want, timeout_s=40):
    """Read telemetry.alerts until `want(alert)` has matched, returning every alert seen."""
    seen: list[dict] = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        batch = await consumer.getmany(timeout_ms=500)
        for msgs in batch.values():
            seen += [json.loads(m.value) for m in msgs]
        if any(want(a) for a in seen):
            return seen
    raise AssertionError(f"expected alert not seen; got {seen}")


async def test_spike_produces_one_open_alert_then_resolves(postgres_url, kafka_bootstrap):
    settings = Settings(
        database_url=postgres_url,
        kafka_bootstrap=kafka_bootstrap,
        topic_partitions=1,
        consumer_group=f"it-{uuid.uuid4()}",
        startup_grace_s=0,
        sweep_interval_s=1,
        rules_refresh_s=1,
        alert_cooldown_s=0,
    )
    engine = make_engine(postgres_url)
    await run_migrations(engine)
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE alerts, rules"))
    store = Store(make_sessions(engine))
    await store.seed_default_rules()

    worker = Worker(settings, store)
    await worker.start()
    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await producer.start()
    alerts_consumer = AIOKafkaConsumer(
        settings.topic_alerts,
        bootstrap_servers=kafka_bootstrap,
        auto_offset_reset="earliest",
        group_id=None,
    )
    await alerts_consumer.start()
    try:
        now = datetime.now(UTC)
        for i in range(25):  # calm baseline
            v = 40.0 + (0.1 if i % 2 else -0.1)
            await producer.send(
                settings.topic_validated,
                reading_bytes(v, now + timedelta(seconds=i)),
                key=b"sensor-001",
            )
        await producer.send(
            settings.topic_validated,
            reading_bytes(95.0, now + timedelta(seconds=25)),
            key=b"sensor-001",
        )

        seen = await collect_alerts(
            alerts_consumer, lambda a: a["ruleType"] == "threshold" and a["state"] == "open"
        )
        opens = [a for a in seen if a["ruleType"] == "threshold" and a["state"] == "open"]
        assert len(opens) == 1
        assert opens[0]["deviceId"] == "sensor-001" and opens[0]["value"] == 95.0
        assert opens[0]["severity"] == "critical"

        # a second reading above the limit must not produce another open alert
        await producer.send(
            settings.topic_validated,
            reading_bytes(96.0, now + timedelta(seconds=26)),
            key=b"sensor-001",
        )
        await producer.send(
            settings.topic_validated,
            reading_bytes(40.0, now + timedelta(seconds=27)),
            key=b"sensor-001",
        )
        seen = await collect_alerts(
            alerts_consumer, lambda a: a["ruleType"] == "threshold" and a["state"] == "resolved"
        )

        async with engine.connect() as c:
            rows = (
                await c.execute(
                    text("SELECT state FROM alerts WHERE rule_type = 'threshold' ORDER BY ts")
                )
            ).all()
        assert [r.state for r in rows] == ["open", "resolved"]
    finally:
        await alerts_consumer.stop()
        await producer.stop()
        await worker.stop()
        await engine.dispose()
        await asyncio.sleep(0)
