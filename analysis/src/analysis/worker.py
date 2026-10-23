"""Kafka consumer, rules refresh and heartbeat sweep wired around the rules engine."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRebalanceListener
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import KafkaError, TopicAlreadyExistsError

from .config import Settings
from .pipeline import AlertPipeline
from .rules.engine import RulesEngine
from .rules.models import Alert
from .serialization import alert_to_dict, parse_reading
from .store import Store

log = logging.getLogger("analysis.worker")

# A consumer that joins its group before a new topic's partitions appear in the cluster metadata
# is assigned nothing, and only notices the partitions on its next metadata refresh (aiokafka's
# default is 5 minutes). Topics are created at startup, so refresh often enough to self-heal fast.
METADATA_MAX_AGE_MS = 5_000


class KafkaAlertPublisher:
    def __init__(self, producer: AIOKafkaProducer, topic: str) -> None:
        self._producer = producer
        self._topic = topic

    async def publish(self, alert: Alert) -> None:
        await self._producer.send_and_wait(
            self._topic,
            value=json.dumps(alert_to_dict(alert)).encode(),
            key=alert.device_id.encode(),
        )


async def retry_forever(what: str, op: Callable[[], Awaitable[object]], max_delay: float = 15.0):
    delay = 1.0
    while True:
        try:
            return await op()
        except (KafkaError, OSError) as exc:
            log.warning("%s failed (%s); retrying in %.0fs", what, exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)


async def ensure_topics(settings: Settings) -> None:
    async def create() -> None:
        admin = AIOKafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap)
        await admin.start()
        try:
            for name in (settings.topic_validated, settings.topic_alerts):
                try:
                    await admin.create_topics([NewTopic(name, settings.topic_partitions, 1)])
                except TopicAlreadyExistsError:
                    pass
        finally:
            await admin.close()

    await retry_forever("ensure topics", create)


class _Reseed(ConsumerRebalanceListener):
    """After a rebalance the newly assigned devices' open alerts must be known to the engine."""

    def __init__(self, worker: Worker) -> None:
        self._worker = worker

    async def on_partitions_revoked(self, revoked) -> None:  # noqa: D401
        return None

    async def on_partitions_assigned(self, assigned) -> None:
        await self._worker.restore_open_state()


class Worker:
    def __init__(self, settings: Settings, store: Store, clock=None) -> None:
        self.settings = settings
        self.store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self.engine = RulesEngine(
            self._clock,
            cooldown_s=settings.alert_cooldown_s,
            startup_grace_s=settings.startup_grace_s,
        )
        self._producer: AIOKafkaProducer | None = None
        self._consumer: AIOKafkaConsumer | None = None
        self._tasks: list[asyncio.Task] = []
        self.pipeline: AlertPipeline | None = None
        self.ready = False

    async def restore_open_state(self) -> None:
        try:
            for rule_id, device_id in await self.store.open_alert_keys():
                self.engine.seed_open(rule_id, device_id)
        except Exception as exc:
            log.warning("could not restore open alerts: %s", exc)

    async def refresh_rules(self) -> None:
        self.engine.set_rules(await self.store.load_rules())

    async def start(self) -> None:
        await ensure_topics(self.settings)
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.settings.kafka_bootstrap, acks="all", enable_idempotence=True
        )
        await retry_forever("start producer", self._producer.start)
        self.pipeline = AlertPipeline(
            self.store, KafkaAlertPublisher(self._producer, self.settings.topic_alerts)
        )
        await self.refresh_rules()
        await self.restore_open_state()

        self._consumer = AIOKafkaConsumer(
            bootstrap_servers=self.settings.kafka_bootstrap,
            group_id=self.settings.consumer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            metadata_max_age_ms=METADATA_MAX_AGE_MS,
        )
        await retry_forever("start consumer", self._consumer.start)
        self._consumer.subscribe([self.settings.topic_validated], listener=_Reseed(self))
        self._tasks = [
            asyncio.create_task(self._consume_loop(), name="consume"),
            asyncio.create_task(self._sweep_loop(), name="sweep"),
            asyncio.create_task(self._rules_loop(), name="rules"),
        ]
        self.ready = True

    async def stop(self) -> None:
        self.ready = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._consumer:
            await self._consumer.stop()
        if self._producer:
            await self._producer.stop()

    async def _consume_loop(self) -> None:
        assert self._consumer and self.pipeline
        while True:
            batch = await self._consumer.getmany(timeout_ms=1000, max_records=500)
            if not batch:
                continue
            alerts: list[Alert] = []
            for msgs in batch.values():
                for m in msgs:
                    reading = parse_reading(m.value)
                    if reading is not None:
                        alerts += self.engine.on_reading(reading)
            await self.pipeline.emit(alerts)  # blocks (retrying) rather than losing alerts
            await self._consumer.commit()

    async def _sweep_loop(self) -> None:
        assert self.pipeline
        while True:
            await asyncio.sleep(self.settings.sweep_interval_s)
            await self.pipeline.emit(self.engine.sweep())

    async def _rules_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.rules_refresh_s)
            try:
                await self.refresh_rules()
            except Exception as exc:  # keep evaluating with the cached rules
                log.warning("rules refresh failed, keeping cached rules: %s", exc)
