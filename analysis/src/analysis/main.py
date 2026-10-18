"""FastAPI application: owns the worker lifecycle. REST and WebSocket routes are added in M3."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings
from .db import make_engine, make_sessions
from .migrate import run_migrations
from .store import Store
from .worker import Worker


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        engine = make_engine(settings.database_url)
        await run_migrations(engine)
        store = Store(make_sessions(engine))
        if settings.seed_default_rules:
            await store.seed_default_rules()
        worker = Worker(settings, store)
        await worker.start()
        app.state.settings = settings
        app.state.store = store
        app.state.worker = worker
        try:
            yield
        finally:
            await worker.stop()
            await engine.dispose()

    app = FastAPI(title="Telemetry analysis API", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    async def ready() -> dict[str, bool]:
        return {"ready": bool(getattr(app.state, "worker", None) and app.state.worker.ready)}

    return app


app = create_app
