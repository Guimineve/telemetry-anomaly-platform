#!/usr/bin/env python3
"""End-to-end smoke test against a running `docker compose up` stack.

Injects faults into the simulator and checks the whole path:
  spike     -> exactly one threshold alert, seen on the WebSocket AND the REST history
  malformed -> counted as rejected by ingestion (routed to the dead-letter topic)
  dropout   -> "Device silent" alert opens, then resolves when the device returns

Everything user-facing goes through the web container (nginx), so its /api and /ws proxying is
exercised too. Requires: pip install httpx websockets
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx
import websockets

WEB = os.getenv("WEB_URL", "http://localhost:3000")
SIM = os.getenv("SIM_URL", "http://localhost:8001")
INGEST = os.getenv("INGEST_URL", "http://localhost:8080")
WS = WEB.replace("http", "ws", 1) + "/ws/alerts"


def log(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[smoke] FAIL: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


async def wait_until(what: str, probe, timeout_s: float = 60, every_s: float = 1.0):
    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = await probe()
            if result:
                return result
        except Exception as exc:  # noqa: BLE001 - services may still be starting
            last_error = exc
        await asyncio.sleep(every_s)
    fail(f"timed out waiting for {what} ({last_error})")


async def rejected_count(http: httpx.AsyncClient) -> float:
    r = await http.get(
        f"{INGEST}/actuator/metrics/ingestion.readings",
        params={"tag": "outcome:rejected"},
    )
    r.raise_for_status()
    return r.json()["measurements"][0]["value"]


async def alerts(http: httpx.AsyncClient, **params) -> list[dict]:
    r = await http.get(f"{WEB}/api/v1/alerts", params={"limit": 200, **params})
    r.raise_for_status()
    return r.json()["items"]


async def post_fault(http: httpx.AsyncClient, **body) -> None:
    r = await http.post(f"{SIM}/faults", json=body)
    if r.status_code != 202:
        fail(f"simulator rejected fault {body}: {r.status_code} {r.text}")


async def main() -> None:
    async with httpx.AsyncClient(timeout=10) as http:
        log("waiting for services")
        for name, url in (
            ("simulator", f"{SIM}/health"),
            ("web", f"{WEB}/healthz"),
            ("analysis api", f"{WEB}/api/v1/rules"),
        ):
            await wait_until(name, lambda url=url: _is_up(http, url), 90)
        rules = (await http.get(f"{WEB}/api/v1/rules")).json()
        if not rules:
            fail("no rules seeded")

        # let the pipeline warm up so a baseline exists
        await asyncio.sleep(5)
        before = await alerts(http, deviceId="sensor-001", state="open")
        known_ids = {a["id"] for a in before}

        async with websockets.connect(WS) as ws:
            await ws.send("ping")
            if await asyncio.wait_for(ws.recv(), 5) != "pong":
                fail("websocket did not answer ping")

            # --- 1. spike -> exactly one threshold alert ---------------------------------
            log("injecting temperature spike on sensor-001")
            t0 = time.monotonic()
            await post_fault(
                http,
                kind="spike",
                deviceId="sensor-001",
                metric="temperature",
                magnitude=50,
            )
            live = None
            deadline = time.monotonic() + 30
            while live is None and time.monotonic() < deadline:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                except TimeoutError:
                    continue
                if (
                    msg["ruleType"] == "threshold"
                    and msg["state"] == "open"
                    and msg["deviceId"] == "sensor-001"
                ):
                    live = msg
            if live is None:
                fail("no threshold alert arrived on the WebSocket within 30s")
            log(
                f"WebSocket alert after {time.monotonic() - t0:.1f}s: {live['message']}"
            )

            history = await wait_until(
                "alert in REST history",
                lambda: _threshold_alerts(http, known_ids),
                15,
            )
            if len(history) != 1 or history[0]["id"] != live["id"]:
                fail(
                    f"expected exactly one threshold alert matching the WebSocket one, got "
                    f"{[a['id'] for a in history]} vs {live['id']}"
                )
            log("REST history agrees: exactly one alert")

            ack = await http.post(f"{WEB}/api/v1/alerts/{live['id']}/ack")
            if ack.status_code != 200 or not ack.json()["acknowledgedAt"]:
                fail(f"ack failed: {ack.status_code} {ack.text}")
            log("alert acknowledged through the API")

        # --- 2. malformed payloads -> dead-letter ----------------------------------------
        rejected_before = await rejected_count(http)
        log("injecting 3 malformed messages")
        await post_fault(http, kind="malformed", deviceId="sensor-003", count=3)
        await wait_until(
            "ingestion to reject the bad messages",
            lambda: _rejected_at_least(http, rejected_before + 3),
            20,
        )
        log("ingestion rejected them (routed to telemetry.dlq)")

        # --- 3. dropout -> missing heartbeat opens, then resolves -------------------------
        log("silencing sensor-002 for 25s")
        await post_fault(http, kind="dropout", deviceId="sensor-002", durationS=25)
        opened = await wait_until(
            "'Device silent' alert",
            lambda: _silent(http, "open"),
            40,
        )
        log(f"silent-device alert opened: {opened['message']}")
        await wait_until(
            "'Device silent' to resolve", lambda: _silent(http, "resolved"), 50
        )
        log("silent-device alert resolved after the device came back")

    log("OK")


async def _is_up(http, url):
    return (await http.get(url)).is_success


async def _threshold_alerts(http, known_ids):
    items = await alerts(http, deviceId="sensor-001", state="open")
    found = [
        a for a in items if a["ruleType"] == "threshold" and a["id"] not in known_ids
    ]
    return found or None


async def _rejected_at_least(http, n):
    return await rejected_count(http) >= n


async def _silent(http, state):
    items = await alerts(http, deviceId="sensor-002", state=state)
    hits = [a for a in items if a["ruleType"] == "missing_heartbeat"]
    return hits[0] if hits else None


if __name__ == "__main__":
    asyncio.run(main())
