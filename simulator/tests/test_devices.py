import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from simulator.devices import Fault, Simulator

T0 = datetime(2026, 1, 1, tzinfo=UTC)
SCHEMA = json.loads((Path(__file__).parents[2] / "contracts" / "reading.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def make() -> Simulator:
    return Simulator(["sensor-001", "sensor-002"], random.Random(42))


def readings(msgs):
    return [json.loads(m.value) for m in msgs if m.valid]


def test_normal_tick_emits_four_valid_readings_per_device():
    msgs = make().tick(T0)
    assert len(msgs) == 8
    for m in msgs:
        assert m.valid
        assert not list(VALIDATOR.iter_errors(json.loads(m.value)))
    assert {r["metric"] for r in readings(msgs)} == {
        "temperature",
        "voltage",
        "pressure",
        "heartbeat",
    }


def test_heartbeat_counter_increments():
    sim = make()
    first = [r for r in readings(sim.tick(T0)) if r["metric"] == "heartbeat"]
    later = readings(sim.tick(T0 + timedelta(seconds=1)))
    second = [r for r in later if r["metric"] == "heartbeat"]
    assert [r["value"] for r in first] == [1, 1]
    assert [r["value"] for r in second] == [2, 2]


def test_reading_ids_are_unique():
    sim = make()
    ids = [r["id"] for t in range(5) for r in readings(sim.tick(T0 + timedelta(seconds=t)))]
    assert len(ids) == len(set(ids))


def test_spike_applies_once_to_target_metric():
    sim = make()
    sim.inject(Fault("spike", "sensor-001", metric="temperature", magnitude=50), T0)
    spiked = [
        r
        for r in readings(sim.tick(T0))
        if r["deviceId"] == "sensor-001" and r["metric"] == "temperature"
    ][0]
    assert spiked["value"] > 80
    after = [
        r
        for r in readings(sim.tick(T0 + timedelta(seconds=1)))
        if r["deviceId"] == "sensor-001" and r["metric"] == "temperature"
    ][0]
    assert after["value"] < 50


def test_drift_ramps_then_ends():
    sim = make()
    sim.inject(Fault("drift", "sensor-001", metric="voltage", magnitude=10, duration_s=10), T0)

    def volt(t):
        return [
            r
            for r in readings(sim.tick(T0 + timedelta(seconds=t)))
            if r["deviceId"] == "sensor-001" and r["metric"] == "voltage"
        ][0]["value"]

    assert volt(5) == pytest.approx(33, abs=0.5)  # halfway: ~28 + 5
    assert volt(9) == pytest.approx(37, abs=0.5)  # ~28 + 9
    assert volt(11) == pytest.approx(28, abs=0.5)  # fault expired


def test_dropout_silences_only_that_device_then_recovers():
    sim = make()
    sim.inject(Fault("dropout", "sensor-001", duration_s=5), T0)
    during = readings(sim.tick(T0 + timedelta(seconds=1)))
    assert {r["deviceId"] for r in during} == {"sensor-002"}
    after = readings(sim.tick(T0 + timedelta(seconds=6)))
    assert {r["deviceId"] for r in after} == {"sensor-001", "sensor-002"}


def test_malformed_payloads_fail_schema_and_are_flagged():
    sim = make()
    sim.inject(Fault("malformed", "sensor-002", count=5), T0)
    bad = [m for m in sim.tick(T0) if not m.valid]
    assert len(bad) == 5
    for m in bad:
        try:
            doc = json.loads(m.value)
        except json.JSONDecodeError:
            continue
        assert list(VALIDATOR.iter_errors(doc)), m.value


def test_inject_rejects_unknown_device_and_metric():
    sim = make()
    with pytest.raises(KeyError):
        sim.inject(Fault("spike", "nope"), T0)
    with pytest.raises(ValueError):
        sim.inject(Fault("spike", "sensor-001", metric="humidity"), T0)
