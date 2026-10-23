#!/usr/bin/env python3
"""Failure-mode checks against a running `docker compose up` stack (docker compose only).

Each phase stops a dependency, causes something that should be alerted on, restarts the
dependency, and asserts nothing was lost:

  mongo     ingestion stops advancing while MongoDB is down and catches up afterwards
  analysis  a spike injected while the analysis service is down is alerted on after restart
  postgres  a spike injected while PostgreSQL is down is alerted on after it returns
  kafka     the pipeline resumes after the broker restarts and a new spike is alerted on

Requires: pip install httpx. Run after scripts/smoke.py (it reuses the same stack).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import httpx

SIM = os.getenv("SIM_URL", "http://localhost:8001")
INGEST = os.getenv("INGEST_URL", "http://localhost:8080")
API = os.getenv(
    "API_URL", "http://localhost:8000"
)  # direct, so nginx DNS caching is not involved
ADMIN = (os.getenv("ADMIN_USERNAME", "admin"), os.getenv("ADMIN_PASSWORD", "admin-dev-password"))


def log(msg: str) -> None:
    print(f"[chaos] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[chaos] FAIL: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], check=True, capture_output=True)


async def wait_until(what, probe, timeout_s, every_s=1.0):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        try:
            result = await probe()
            if result:
                return result
        except Exception as exc:  # noqa: BLE001 - the thing under test is down
            last = exc
        await asyncio.sleep(every_s)
    fail(f"timed out waiting for {what} ({last})")


async def accepted(http) -> float:
    r = await http.get(
        f"{INGEST}/actuator/metrics/ingestion.readings",
        params={"tag": "outcome:accepted"},
    )
    r.raise_for_status()
    return r.json()["measurements"][0]["value"]


async def spike(http, device: str) -> None:
    r = await http.post(
        f"{SIM}/faults",
        json={
            "kind": "spike",
            "deviceId": device,
            "metric": "temperature",
            "magnitude": 50,
        },
    )
    if r.status_code != 202:
        fail(f"spike on {device} rejected: {r.status_code} {r.text}")


async def threshold_alert(http, device: str):
    r = await http.get(
        f"{API}/api/v1/alerts",
        params={"deviceId": device, "state": "open", "limit": 50},
    )
    r.raise_for_status()
    hits = [a for a in r.json()["items"] if a["ruleType"] == "threshold"]
    return hits[0] if hits else None


async def api_ready(http) -> bool:
    return (await http.get(f"{API}/ready")).is_success


async def mongo_phase(http) -> None:
    log("MongoDB down: ingestion must stop advancing, then catch up")
    compose("stop", "mongo")
    await asyncio.sleep(8)  # let any in-flight batch settle
    a = await accepted(http)
    await asyncio.sleep(10)
    b = await accepted(http)
    if b != a:
        fail(f"ingestion kept accepting readings with MongoDB down ({a} -> {b})")
    log(f"ingestion held at {b:.0f} accepted readings while MongoDB was down")
    compose("start", "mongo")

    async def caught_up():
        return await accepted(http) > b + 100

    await wait_until("ingestion to catch up after MongoDB returned", caught_up, 150)
    log("ingestion caught up from the Kafka backlog")


async def analysis_phase(http) -> None:
    log("analysis service down: a spike during the outage must still be alerted on")
    compose("stop", "analysis")
    await spike(http, "sensor-004")
    await asyncio.sleep(5)
    compose("start", "analysis")
    await wait_until("analysis to be ready", lambda: api_ready(http), 90)
    alert = await wait_until(
        "alert for the spike injected during the outage",
        lambda: threshold_alert(http, "sensor-004"),
        60,
    )
    log(f"alert delivered after restart: {alert['message']}")


async def postgres_phase(http) -> None:
    log("PostgreSQL down: the alert must be retried, not dropped")
    compose("stop", "postgres")
    await asyncio.sleep(3)
    await spike(http, "sensor-005")
    await asyncio.sleep(8)
    compose("start", "postgres")
    alert = await wait_until(
        "alert persisted after PostgreSQL returned",
        lambda: threshold_alert(http, "sensor-005"),
        90,
    )
    log(f"alert persisted after the database returned: {alert['message']}")


async def kafka_phase(http) -> None:
    log("Kafka down: the pipeline must resume after the broker restarts")
    before = await accepted(http)
    compose("stop", "kafka")
    await asyncio.sleep(15)
    compose("start", "kafka")

    async def resumed():
        return await accepted(http) > before + 50

    await wait_until("ingestion to resume after Kafka restarted", resumed, 150)
    log("readings flow again")
    await spike(http, "sensor-003")
    alert = await wait_until(
        "alert after Kafka recovered", lambda: threshold_alert(http, "sensor-003"), 90
    )
    log(f"end-to-end alerting works again: {alert['message']}")


async def login() -> str:
    """Session cookie for the admin. Tokens are stateless, so it survives service restarts."""
    async with httpx.AsyncClient(timeout=10) as anon:
        r = await anon.post(
            f"{API}/api/v1/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}
        )
    if r.status_code != 200:
        fail(f"admin login failed: {r.status_code} {r.text}")
    return r.cookies["session"]


async def main() -> None:
    session = {"Cookie": f"session={await login()}"}
    async with httpx.AsyncClient(timeout=10, headers=session) as http:
        await mongo_phase(http)
        await analysis_phase(http)
        await postgres_phase(http)
        await kafka_phase(http)
    log("OK")


if __name__ == "__main__":
    asyncio.run(main())
