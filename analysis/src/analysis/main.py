"""FastAPI application: owns the worker lifecycle, REST routes, auth and the alert WebSocket."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status

from .api.auth_routes import auth_router, users_router
from .api.deps import SESSION_COOKIE, user_from_token
from .api.routes import router
from .auth.passwords import hash_password
from .auth.ratelimit import AttemptLimiter
from .auth.tokens import TokenCodec
from .broadcast import Broadcaster
from .config import Settings
from .db import make_engine, make_sessions
from .migrate import run_migrations
from .store import Store
from .worker import Worker

log = logging.getLogger("analysis")

MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 10


def check_settings(settings: Settings) -> None:
    """Fail fast on missing secrets, a weak signing key or half-configured admin credentials."""
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be set")
    if len(settings.jwt_secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(f"JWT_SECRET must be set to at least {MIN_SECRET_LENGTH} characters")
    if bool(settings.admin_username) != bool(settings.admin_password):
        raise RuntimeError("set both ADMIN_USERNAME and ADMIN_PASSWORD, or neither")
    if settings.admin_password and len(settings.admin_password) < MIN_PASSWORD_LENGTH:
        raise RuntimeError(f"ADMIN_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters")
    if settings.cookie_secure not in ("auto", "true", "false"):
        raise RuntimeError("COOKIE_SECURE must be auto, true or false")


async def bootstrap_admin(store: Store, settings: Settings) -> bool:
    """Create the admin from ADMIN_USERNAME/ADMIN_PASSWORD if no user has that name yet.

    An existing user is never modified, so changing ADMIN_PASSWORD later does not reset it.
    Returns True if the admin was created.
    """
    if not settings.admin_username:
        log.warning("ADMIN_USERNAME is not set: there is no admin until it is")
        return False
    if await store.get_user_by_username(settings.admin_username) is not None:
        return False
    created = await store.create_user(
        settings.admin_username, hash_password(settings.admin_password), "admin"
    )
    if created is not None:
        log.info("created admin user %r", settings.admin_username)
    return created is not None


def create_app(settings: Settings | None = None, run_worker: bool = True) -> FastAPI:
    """`run_worker=False` builds the HTTP surface only (OpenAPI export, tests)."""
    settings = settings or Settings.from_env()
    if run_worker:
        check_settings(settings)

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
        await bootstrap_admin(store, settings)
        worker = Worker(settings, store)
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
    app.state.settings = settings
    app.state.tokens = TokenCodec(settings.jwt_secret, settings.session_ttl_s)
    app.state.login_limiter = AttemptLimiter(max_attempts=5, window_s=60)
    app.state.register_limiter = AttemptLimiter(max_attempts=10, window_s=600)
    app.state.broadcaster = Broadcaster()
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(router)

    @app.websocket("/ws/alerts")
    async def ws_alerts(ws: WebSocket) -> None:
        """Pushes each alert (see contracts/alert.schema.json) as a JSON text frame.

        Needs the session cookie. A browser always sends Origin, so a cross-site page cannot
        open the socket with the user's cookie (SameSite does not cover WebSockets everywhere).
        """
        origin = ws.headers.get("origin")
        if origin and urlsplit(origin).netloc != ws.headers.get("host"):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        token = ws.cookies.get(SESSION_COOKIE)
        if await user_from_token(app.state.store, app.state.tokens, token) is None:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
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

