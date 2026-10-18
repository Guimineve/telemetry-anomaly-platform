from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from analysis.config import Settings
from analysis.main import create_app

from .fakes import FakeStore

THRESHOLD = {
    "name": "temp high",
    "type": "threshold",
    "metric": "temperature",
    "params": {"max": 60},
    "severity": "critical",
}


@pytest.fixture
def env():
    """App with routes and WebSocket but no lifespan (no Kafka, no Postgres)."""
    app = create_app(Settings(), run_worker=False)
    store = FakeStore()
    app.state.store = store
    changed = []

    async def on_change():
        changed.append(1)

    app.state.on_rules_changed = on_change
    return TestClient(app), store, changed, app


def test_rule_crud_roundtrip(env):
    c, _, changed, _ = env
    r = c.post("/api/v1/rules", json=THRESHOLD)
    assert r.status_code == 201
    rule = r.json()
    assert rule["devicePattern"] == "*" and rule["enabled"] is True and rule["id"]
    assert len(changed) == 1  # engine is told immediately

    assert c.get(f"/api/v1/rules/{rule['id']}").json()["name"] == "temp high"
    assert [x["id"] for x in c.get("/api/v1/rules").json()] == [rule["id"]]

    upd = {**THRESHOLD, "name": "renamed", "params": {"max": 70}, "enabled": False}
    r = c.put(f"/api/v1/rules/{rule['id']}", json=upd)
    assert r.status_code == 200 and r.json()["name"] == "renamed"
    assert r.json()["params"] == {"max": 70} and r.json()["enabled"] is False

    assert c.delete(f"/api/v1/rules/{rule['id']}").status_code == 204
    assert c.get(f"/api/v1/rules/{rule['id']}").status_code == 404
    assert len(changed) == 3


@pytest.mark.parametrize(
    "body",
    [
        {**THRESHOLD, "params": {}},  # threshold needs a bound
        {**THRESHOLD, "params": {"min": 5, "max": 1}},  # min >= max
        {**THRESHOLD, "params": {"max": "high"}},  # not a number
        {**THRESHOLD, "params": {"max": 1, "bogus": 2}},  # unknown key
        {**THRESHOLD, "metric": None},  # metric required
        {**THRESHOLD, "metric": "humidity"},  # unknown metric
        {**THRESHOLD, "type": "magic"},  # unknown type
        {**THRESHOLD, "severity": "meh"},
        {**THRESHOLD, "name": ""},
        {"name": "r", "type": "rate_of_change", "metric": "voltage", "params": {}},
        {
            "name": "r",
            "type": "rate_of_change",
            "metric": "voltage",
            "params": {"max_rate_per_s": -1},
        },
        {
            "name": "z",
            "type": "zscore",
            "metric": "voltage",
            "params": {"window": 10, "min_samples": 50},
        },
        {"name": "h", "type": "missing_heartbeat", "params": {}},
        {
            "name": "h",
            "type": "missing_heartbeat",
            "metric": "temperature",
            "params": {"timeout_s": 5},
        },
    ],
)
def test_invalid_rules_are_rejected(env, body):
    c, store, _, _ = env
    assert c.post("/api/v1/rules", json=body).status_code == 422
    assert not store.rules


def test_valid_rule_of_every_type_is_accepted(env):
    c, *_ = env
    bodies = [
        THRESHOLD,
        {
            "name": "roc",
            "type": "rate_of_change",
            "metric": "voltage",
            "params": {"max_rate_per_s": 2},
        },
        {
            "name": "hb",
            "type": "missing_heartbeat",
            "params": {"timeout_s": 15},
            "devicePattern": "sensor-0*",
        },
        {"name": "z", "type": "zscore", "metric": "pressure", "params": {}},
    ]
    for b in bodies:
        assert c.post("/api/v1/rules", json=b).status_code == 201, b


def test_unknown_rule_is_404(env):
    c, *_ = env
    assert c.get("/api/v1/rules/nope").status_code == 404
    assert c.put("/api/v1/rules/nope", json=THRESHOLD).status_code == 404
    assert c.delete("/api/v1/rules/nope").status_code == 404


def test_alert_history_filters_and_pagination(env):
    c, store, _, _ = env
    for i in range(5):
        store.add_alert(ts=datetime(2026, 1, 1, 0, 0, i, tzinfo=UTC), device_id="d1")
    store.add_alert(
        device_id="d2", severity="info", state="resolved", ts=datetime(2026, 1, 2, tzinfo=UTC)
    )

    page = c.get("/api/v1/alerts", params={"limit": 2}).json()
    assert page["total"] == 6 and len(page["items"]) == 2 and page["limit"] == 2
    assert page["items"][0]["deviceId"] == "d2"  # newest first
    assert page["items"][0]["timestamp"].startswith("2026-01-02")

    assert c.get("/api/v1/alerts", params={"deviceId": "d1"}).json()["total"] == 5
    assert c.get("/api/v1/alerts", params={"severity": "info"}).json()["total"] == 1
    assert c.get("/api/v1/alerts", params={"state": "resolved"}).json()["total"] == 1
    assert c.get("/api/v1/alerts", params={"limit": 2, "offset": 4}).json()["items"].__len__() == 2
    since = c.get("/api/v1/alerts", params={"since": "2026-01-01T00:00:03Z"}).json()
    assert since["total"] == 3

    assert c.get("/api/v1/alerts", params={"limit": 0}).status_code == 422
    assert c.get("/api/v1/alerts", params={"limit": 1000}).status_code == 422
    assert c.get("/api/v1/alerts", params={"severity": "bad"}).status_code == 422


def test_ack_is_idempotent_and_filterable(env):
    c, store, _, _ = env
    a = store.add_alert()
    store.add_alert()
    first = c.post(f"/api/v1/alerts/{a.id}/ack").json()
    assert first["acknowledgedAt"] is not None
    second = c.post(f"/api/v1/alerts/{a.id}/ack").json()
    assert second["acknowledgedAt"] == first["acknowledgedAt"]
    assert c.get("/api/v1/alerts", params={"acknowledged": "true"}).json()["total"] == 1
    assert c.get("/api/v1/alerts", params={"acknowledged": "false"}).json()["total"] == 1
    assert c.post("/api/v1/alerts/nope/ack").status_code == 404
    assert c.get("/api/v1/alerts/nope").status_code == 404
    assert c.get(f"/api/v1/alerts/{a.id}").json()["id"] == a.id


def test_websocket_receives_broadcast_and_answers_ping(env):
    c, _, _, app = env
    with c, c.websocket_connect("/ws/alerts") as ws:
        ws.send_text("ping")
        assert ws.receive_text() == "pong"
        c.portal.call(app.state.broadcaster.broadcast, '{"id":"a1","state":"open"}')
        assert ws.receive_text() == '{"id":"a1","state":"open"}'
        assert app.state.broadcaster.client_count == 1
    assert app.state.broadcaster.client_count == 0
