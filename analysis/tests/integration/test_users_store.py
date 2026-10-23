"""Real SQL for users: unique usernames under concurrency, lookups, updates."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from analysis.config import Settings
from analysis.db import make_engine, make_sessions
from analysis.main import bootstrap_admin
from analysis.migrate import run_migrations
from analysis.store import Store

pytestmark = pytest.mark.integration


async def test_user_queries(postgres_url):
    engine = make_engine(postgres_url)
    try:
        await run_migrations(engine)
        async with engine.begin() as c:
            await c.execute(text("TRUNCATE users"))
        store = Store(make_sessions(engine))

        alice = await store.create_user("alice", "hash-a", "viewer")
        assert alice is not None and alice.is_active and alice.created_at is not None
        assert await store.create_user("alice", "hash-b", "admin") is None  # taken

        # two replicas bootstrapping the same admin at once create exactly one row
        results = await asyncio.gather(
            *(store.create_user("boss", f"h{i}", "admin") for i in range(5))
        )
        assert sum(r is not None for r in results) == 1

        assert (await store.get_user_by_username("alice")).id == alice.id
        assert await store.get_user_by_username("nobody") is None
        assert (await store.get_user(alice.id)).username == "alice"
        assert await store.get_user("missing") is None
        assert [u.username for u in await store.list_users()] == ["alice", "boss"]

        updated = await store.update_user(alice.id, {"role": "admin", "is_active": False})
        assert updated.role == "admin" and updated.is_active is False
        assert await store.update_user("missing", {"role": "admin"}) is None

        assert alice.token_version == 0  # migration 0003 default
        changed = await store.set_password(alice.id, "hash-new")
        assert changed.password_hash == "hash-new" and changed.token_version == 1
        assert (await store.bump_token_version(alice.id)).token_version == 2
        assert (await store.get_user(alice.id)).token_version == 2
        assert await store.set_password("missing", "h") is None
        assert await store.bump_token_version("missing") is None

        now = datetime.now(UTC)
        await store.record_login(alice.id, now)
        assert (await store.get_user(alice.id)).last_login_at == now

        settings = Settings(jwt_secret="x" * 32, admin_username="root", admin_password="p" * 10)
        assert await bootstrap_admin(store, settings) is True
        assert await bootstrap_admin(store, settings) is False
        assert (await store.get_user_by_username("root")).role == "admin"
    finally:
        await engine.dispose()
