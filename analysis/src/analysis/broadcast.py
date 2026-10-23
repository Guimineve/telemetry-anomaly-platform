"""Fan telemetry.alerts out to WebSocket clients.

Each replica consumes the whole alerts topic under its own unique consumer group, so every
replica pushes every alert to the browsers connected to it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Protocol

from aiokafka import AIOKafkaConsumer

from .worker import METADATA_MAX_AGE_MS, retry_forever

log = logging.getLogger("analysis.broadcast")


class Client(Protocol):
    async def send_text(self, data: str) -> None: ...


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[Client] = set()
        self._task: asyncio.Task | None = None
        self._consumer: AIOKafkaConsumer | None = None

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def add(self, client: Client) -> None:
        self._clients.add(client)

    def remove(self, client: Client) -> None:
        self._clients.discard(client)

    async def broadcast(self, text: str) -> None:
        for client in list(self._clients):
            try:
                await client.send_text(text)
            except Exception:  # a dead socket must not affect the others
                self._clients.discard(client)

    async def start(self, bootstrap: str, topic: str) -> None:
        self._consumer = AIOKafkaConsumer(
            bootstrap_servers=bootstrap,
            group_id=f"ws-{uuid.uuid4().hex}",
            enable_auto_commit=False,
            auto_offset_reset="latest",
            metadata_max_age_ms=METADATA_MAX_AGE_MS,
        )
        await retry_forever("start broadcaster consumer", self._consumer.start)
        self._consumer.subscribe([topic])
        self._task = asyncio.create_task(self._run(), name="broadcast")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._consumer:
            await self._consumer.stop()

    async def _run(self) -> None:
        assert self._consumer
        async for msg in self._consumer:
            await self.broadcast(msg.value.decode())
