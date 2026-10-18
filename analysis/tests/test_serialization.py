import json
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from analysis.rules.models import Alert
from analysis.serialization import alert_to_dict, parse_reading

ALERT_SCHEMA = json.loads(
    (Path(__file__).parents[2] / "contracts" / "alert.schema.json").read_text()
)
READING = (
    b'{"id":"3f2b8c1e-5a6d-4e7f-9a10-b2c3d4e5f607","deviceId":"sensor-001",'
    b'"metric":"temperature","value":41.2,"unit":"celsius","timestamp":"2026-01-01T10:00:00.123Z"}'
)


def test_parse_reading_ok():
    r = parse_reading(READING)
    assert r.device_id == "sensor-001" and r.value == 41.2
    assert r.timestamp == datetime(2026, 1, 1, 10, 0, 0, 123000, tzinfo=UTC)


def test_parse_reading_garbage_is_none():
    assert parse_reading(b"nope") is None
    assert parse_reading(None) is None
    assert parse_reading(b'{"id": "x"}') is None


def test_alert_dict_matches_contract():
    a = Alert(
        "a1",
        "r1",
        "rule",
        "threshold",
        "sensor-001",
        "temperature",
        "critical",
        "open",
        "too hot",
        90.0,
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    doc = alert_to_dict(a)
    assert not list(Draft202012Validator(ALERT_SCHEMA).iter_errors(doc))
    assert doc["timestamp"] == "2026-01-01T00:00:00.000Z"


def test_heartbeat_alert_with_null_metric_and_value_matches_contract():
    a = Alert(
        "a2",
        "r2",
        "silent",
        "missing_heartbeat",
        "sensor-001",
        None,
        "critical",
        "open",
        "no heartbeat",
        None,
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert not list(Draft202012Validator(ALERT_SCHEMA).iter_errors(alert_to_dict(a)))
