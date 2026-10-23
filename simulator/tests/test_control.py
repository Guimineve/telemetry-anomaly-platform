import random

from fastapi.testclient import TestClient

from simulator.control import Stats, create_app
from simulator.devices import Simulator

TOKEN = "test-token"


def client(token: str = TOKEN):
    sim = Simulator(["sensor-001"], random.Random(1))
    c = TestClient(create_app(sim, Stats(), token))
    c.headers["Authorization"] = f"Bearer {TOKEN}"
    return c, sim


def test_control_endpoints_need_the_token():
    c, sim = client()
    body = {"kind": "spike", "deviceId": "sensor-001"}
    for auth in (None, "Bearer wrong", f"Basic {TOKEN}", TOKEN):
        headers = {} if auth is None else {"Authorization": auth}
        anon = TestClient(c.app)
        assert anon.post("/faults", json=body, headers=headers).status_code == 401, auth
        assert anon.get("/devices", headers=headers).status_code == 401, auth
    assert not sim.active_faults()
    assert TestClient(c.app).get("/health").status_code == 200


def test_control_api_is_disabled_without_a_configured_token():
    c, sim = client(token="")
    assert c.post("/faults", json={"kind": "spike", "deviceId": "sensor-001"}).status_code == 503
    assert c.get("/health").status_code == 200
    assert not sim.active_faults()


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
