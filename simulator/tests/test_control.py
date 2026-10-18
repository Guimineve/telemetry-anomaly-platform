import random

from fastapi.testclient import TestClient

from simulator.control import Stats, create_app
from simulator.devices import Simulator


def client():
    sim = Simulator(["sensor-001"], random.Random(1))
    return TestClient(create_app(sim, Stats())), sim


def test_post_fault_accepted_and_visible():
    c, sim = client()
    r = c.post("/faults", json={"kind": "dropout", "deviceId": "sensor-001", "durationS": 5})
    assert r.status_code == 202
    assert sim.active_faults()[0].kind == "dropout"
    assert c.get("/devices").json()["activeFaults"][0]["deviceId"] == "sensor-001"


def test_unknown_device_is_404():
    c, _ = client()
    assert c.post("/faults", json={"kind": "spike", "deviceId": "x"}).status_code == 404


def test_bad_metric_is_422():
    c, _ = client()
    r = c.post("/faults", json={"kind": "spike", "deviceId": "sensor-001", "metric": "humidity"})
    assert r.status_code == 422
