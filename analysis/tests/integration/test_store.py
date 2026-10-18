from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from analysis.db import make_engine, make_sessions
from analysis.migrate import run_migrations
from analysis.rules.models import RULE_TYPES, Alert
from analysis.store import Store

pytestmark = pytest.mark.integration
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def alert(i, rule, device, state, at):
    return Alert(
        f"a{i}",
        rule,
        rule,
        "threshold",
        device,
        "temperature",
        "info",
        state,
        "m",
        1.0,
        T0 + timedelta(seconds=at),
    )


async def test_migrations_seed_rules_and_open_alert_lookup(postgres_url):
    engine = make_engine(postgres_url)
    try:
        await run_migrations(engine)
        await run_migrations(engine)  # idempotent
        async with engine.begin() as c:
            await c.execute(text("TRUNCATE alerts, rules"))
        store = Store(make_sessions(engine))

        assert await store.seed_default_rules() == 6
        assert await store.seed_default_rules() == 0  # only seeds an empty table
        assert {r.type for r in await store.load_rules()} == set(RULE_TYPES)

        await store.save_alerts(
            [
                alert(1, "r1", "d1", "open", 0),
                alert(2, "r1", "d1", "resolved", 1),  # latest for (r1, d1) is resolved
                alert(3, "r2", "d1", "open", 0),  # still open
                alert(4, "r1", "d2", "open", 0),  # still open
            ]
        )
        assert sorted(await store.open_alert_keys()) == [("r1", "d2"), ("r2", "d1")]
    finally:
        await engine.dispose()
