"""Real SQL for the REST queries: filters, ordering, pagination, ack, rule CRUD."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from analysis.db import make_engine, make_sessions
from analysis.migrate import run_migrations
from analysis.rules.models import Alert
from analysis.store import Store

pytestmark = pytest.mark.integration
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def alert(i, device, severity, state, at, rule="r1"):
    return Alert(
        f"a{i}",
        rule,
        "rule",
        "threshold",
        device,
        "temperature",
        severity,
        state,
        "m",
        1.0,
        T0 + timedelta(seconds=at),
    )


async def test_alert_queries_and_rule_crud(postgres_url):
    engine = make_engine(postgres_url)
    try:
        await run_migrations(engine)
        async with engine.begin() as c:
            await c.execute(text("TRUNCATE alerts, rules"))
        store = Store(make_sessions(engine))

        await store.save_alerts(
            [
                alert(1, "d1", "critical", "open", 0),
                alert(2, "d1", "critical", "resolved", 5),
                alert(3, "d2", "info", "open", 10, rule="r2"),
                alert(4, "d2", "warning", "open", 20),
            ]
        )

        rows, total = await store.list_alerts(limit=2)
        assert total == 4 and [r.id for r in rows] == ["a4", "a3"]  # newest first
        rows, _ = await store.list_alerts(limit=2, offset=2)
        assert [r.id for r in rows] == ["a2", "a1"]
        assert (await store.list_alerts(device_id="d1"))[1] == 2
        assert (await store.list_alerts(severity="info"))[1] == 1
        assert (await store.list_alerts(state="open"))[1] == 3
        assert (await store.list_alerts(rule_id="r2"))[1] == 1
        assert (
            await store.list_alerts(
                since=T0 + timedelta(seconds=5), until=T0 + timedelta(seconds=10)
            )
        )[1] == 2

        now = datetime.now(UTC)
        acked = await store.ack_alert("a1", now)
        assert acked is not None and acked.acknowledged_at is not None
        again = await store.ack_alert("a1", now + timedelta(hours=1))
        assert again.acknowledged_at == acked.acknowledged_at  # idempotent
        assert (await store.list_alerts(acknowledged=True))[1] == 1
        assert (await store.list_alerts(acknowledged=False))[1] == 3
        assert await store.ack_alert("missing", now) is None

        rule = await store.create_rule(
            {
                "name": "n",
                "type": "threshold",
                "metric": "temperature",
                "device_pattern": "*",
                "params": {"max": 5},
                "severity": "info",
                "enabled": True,
            }
        )
        assert rule.id and rule.created_at is not None
        updated = await store.update_rule(rule.id, {"name": "n2", "params": {"max": 9}})
        assert updated.name == "n2" and updated.params == {"max": 9}
        assert [r.name for r in await store.list_rules()] == ["n2"]
        assert await store.delete_rule(rule.id) is True
        assert await store.delete_rule(rule.id) is False
        assert await store.update_rule("missing", {}) is None
    finally:
        await engine.dispose()
