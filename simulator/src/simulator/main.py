"""Entry point: publish telemetry to Kafka and serve the fault-control API."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import UTC, datetime

import uvicorn
from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from .control import Stats, create_app
from .devices import Simulator

log = logging.getLogger("simulator")


async def connect(bootstrap: str) -> AIOKafkaProducer:
    """Start a producer, retrying until Kafka is reachable."""
    delay = 1.0
    while True:
        producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap,
            acks="all",
            enable_idempotence=True,
            linger_ms=5,
            request_timeout_ms=10_000,
        )
        try:
            await producer.start()
            return producer
        except KafkaError as exc:
            log.warning("kafka not ready (%s); retrying in %.0fs", exc, delay)
            await producer.stop()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15.0)


async def publish_loop(
    sim: Simulator, producer: AIOKafkaProducer, topic: str, interval_s: float, stats: Stats
) -> None:
    loop = asyncio.get_running_loop()

    def on_done(fut: asyncio.Future) -> None:
        if fut.cancelled() or fut.exception() is not None:
            stats.failed += 1
        else:
            stats.sent += 1

    while True:
        started = loop.time()
        for msg in sim.tick(datetime.now(UTC)):
            try:
                fut = await producer.send(topic, value=msg.value, key=msg.key.encode())
                fut.add_done_callback(on_done)
            except KafkaError:
                stats.failed += 1
        await asyncio.sleep(max(0.0, interval_s - (loop.time() - started)))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    bootstrap = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    topic = os.getenv("TOPIC_RAW", "telemetry.raw")
    n_devices = int(os.getenv("DEVICES", "5"))
    interval_s = float(os.getenv("TICK_INTERVAL_S", "1.0"))
    port = int(os.getenv("CONTROL_PORT", "8001"))
    token = os.getenv("SIMULATOR_TOKEN", "")
    if not token:
        log.warning("SIMULATOR_TOKEN is not set: the fault-injection API is disabled")

    sim = Simulator([f"sensor-{i:03d}" for i in range(1, n_devices + 1)], random.Random())
    stats = Stats()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(sim, stats, token), host="0.0.0.0", port=port, log_level="warning"
        )
    )
    api_task = asyncio.create_task(server.serve())  # control API is up while Kafka connects
    producer = await connect(bootstrap)
    log.info("publishing %d devices to %s every %.2fs", n_devices, topic, interval_s)
    try:
        await publish_loop(sim, producer, topic, interval_s, stats)
    finally:
        await producer.stop()
        server.should_exit = True
        await api_task


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
