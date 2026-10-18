"""FastAPI application: owns the worker lifecycle. REST and WebSocket routes are added in M3."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from .api.routes import router
from .broadcast import Broadcaster
from .config import Settings
from .db import make_engine, make_sessions
from .migrate import run_migrations
from .store import Store
from .worker import Worker


def create_app(settings: Settings | None = None, run_worker: bool = True) -> FastAPI:
    """`run_worker=False` builds the HTTP surface only (OpenAPI export, tests)."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not run_worker:
            yield
            return
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        engine = make_engine(settings.database_url)
        await run_migrations(engine)
        store = Store(make_sessions(engine))
        if settings.seed_default_rules:
            await store.seed_default_rules()
        worker = Worker(settings, store)
        app.state.settings = settings
        app.state.store = store
        app.state.worker = worker
        app.state.on_rules_changed = worker.refresh_rules
        await worker.start()
        await app.state.broadcaster.start(settings.kafka_bootstrap, settings.topic_alerts)
        try:
            yield
        finally:
            await app.state.broadcaster.stop()
            await worker.stop()
            await engine.dispose()

    app = FastAPI(
        title="Telemetry analysis API",
        version="0.1.0",
        description="Rules CRUD, alert history and a live alert WebSocket (`/ws/alerts`).",
        lifespan=lifespan,
    )
    app.state.broadcaster = Broadcaster()
    app.include_router(router)

    @app.websocket("/ws/alerts")
    async def ws_alerts(ws: WebSocket) -> None:
        """Pushes each alert (see contracts/alert.schema.json) as a JSON text frame."""
        await ws.accept()
        app.state.broadcaster.add(ws)
        try:
            while True:
                if await ws.receive_text() == "ping":
                    await ws.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            app.state.broadcaster.remove(ws)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    async def ready() -> dict[str, bool]:
        worker = getattr(app.state, "worker", None)
        if worker is None or not worker.ready:
            raise HTTPException(503, "worker not started")
        return {"ready": True}

    return app

