"""Persist-then-publish for alerts, retrying until each step succeeds.

Retrying (rather than dropping) is deliberate: the caller only commits the Kafka offset after
`emit` returns, so a database or broker outage stalls the consumer instead of losing alerts.
Persistence and publishing retry separately so a publish failure never re-inserts rows.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from typing import Protocol

from .rules.models import Alert

log = logging.getLogger("analysis.pipeline")


class AlertSink(Protocol):
    async def save_alerts(self, alerts: Sequence[Alert]) -> None: ...


class AlertPublisher(Protocol):
    async def publish(self, alert: Alert) -> None: ...


class AlertPipeline:
    def __init__(
        self,
        store: AlertSink,
        publisher: AlertPublisher,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_backoff_s: float = 15.0,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._sleep = sleep
        self._max_backoff = max_backoff_s

    async def emit(self, alerts: Sequence[Alert]) -> None:
        if not alerts:
            return
        await self._retry("persist alerts", lambda: self._store.save_alerts(alerts))
        for alert in alerts:
            await self._retry("publish alert", partial(self._publisher.publish, alert))

    async def _retry(self, what: str, op: Callable[[], Awaitable[None]]) -> None:
        delay = 0.5
        while True:
            try:
                await op()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("%s failed (%s); retrying in %.1fs", what, exc, delay)
                await self._sleep(delay)
                delay = min(delay * 2, self._max_backoff)
