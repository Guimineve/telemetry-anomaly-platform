#!/usr/bin/env python3
"""End-to-end smoke test against a running `docker compose up` stack.

Injects faults into the simulator and checks the whole path:
  spike     -> exactly one threshold alert, seen on the WebSocket AND the REST history
  malformed -> counted as rejected by ingestion (routed to the dead-letter topic)
  dropout   -> "Device silent" alert opens, then resolves when the device returns
  auth      -> no session is refused (REST and WebSocket); a registered viewer cannot change rules

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
INGEST = os.getenv("INGEST_URL", "http://localhost:8081")  # Actuator port
WS = WEB.replace("http", "ws", 1) + "/ws/alerts"
SIM_AUTH = {"Authorization": f"Bearer {os.getenv('SIMULATOR_TOKEN', 'simulator-dev-token')}"}
ADMIN = (os.getenv("ADMIN_USERNAME", "admin"), os.getenv("ADMIN_PASSWORD", "admin-dev-password"))


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


async def login(username: str, password: str) -> str:
    """Log in through the web container and return the session cookie value."""
    async with httpx.AsyncClient(timeout=10) as anon:
        r = await anon.post(
            f"{WEB}/api/v1/auth/login", json={"username": username, "password": password}
        )
    if r.status_code != 200:
        raise RuntimeError(f"login as {username} failed: {r.status_code} {r.text}")
    return r.cookies["session"]


async def check_headers() -> None:
    """The web container sends security headers; the app page gets a strict CSP."""
    async with httpx.AsyncClient(timeout=10) as anon:
        page = await anon.get(f"{WEB}/")
        api = await anon.get(f"{WEB}/api/v1/auth/me")
    for name, r in (("page", page), ("api", api)):
        for header, value in (
            ("x-content-type-options", "nosniff"),
            ("x-frame-options", "DENY"),
            ("referrer-policy", "strict-origin-when-cross-origin"),
        ):
            if r.headers.get(header) != value:
                fail(f"{name}: {header} is {r.headers.get(header)!r}, expected {value!r}")
    csp = page.headers.get("content-security-policy", "")
    if "default-src 'self'" not in csp or "frame-ancestors 'none'" not in csp:
        fail(f"page CSP missing or weak: {csp!r}")
    if "nginx/" in page.headers.get("server", ""):
        fail("nginx version is exposed in the Server header")
    if "strict-transport-security" in page.headers:
        fail("HSTS sent on a plain-HTTP request")

    # behind a TLS-terminating ingress: HSTS and a Secure session cookie
    https = {"X-Forwarded-Proto": "https"}
    async with httpx.AsyncClient(timeout=10) as anon:
        page = await anon.get(f"{WEB}/", headers=https)
        r = await anon.post(
            f"{WEB}/api/v1/auth/login",
            json={"username": ADMIN[0], "password": ADMIN[1]},
            headers=https,
        )
    if not page.headers.get("strict-transport-security", "").startswith("max-age="):
        fail("no HSTS header behind an HTTPS ingress")
    if r.status_code != 200 or "secure" not in r.headers.get("set-cookie", "").lower():
        fail(f"session cookie is not Secure behind an HTTPS ingress: {r.headers.get('set-cookie')}")
    log("security headers present")


async def check_auth() -> None:
    async with httpx.AsyncClient(timeout=10) as anon:
        r = await anon.get(f"{WEB}/api/v1/alerts")
        if r.status_code != 401:
            fail(f"alerts without a session returned {r.status_code}, expected 401")
        try:
            async with websockets.connect(WS):
                fail("WebSocket accepted a connection without a session")
        except websockets.exceptions.InvalidStatus:
            pass

        name = f"smoke{int(time.time())}"
        r = await anon.post(
            f"{WEB}/api/v1/auth/register", json={"username": name, "password": "smoke-password"}
        )
        if r.status_code != 201 or r.json()["role"] != "viewer":
            fail(f"registration failed: {r.status_code} {r.text}")
        viewer = {"Cookie": f"session={r.cookies['session']}"}
        rule = {"name": "x", "type": "threshold", "metric": "voltage", "params": {"max": 1}}
        r = await anon.post(f"{WEB}/api/v1/rules", json=rule, headers=viewer)
        if r.status_code != 403:
            fail(f"viewer created a rule: {r.status_code}, expected 403")
        if (await anon.get(f"{WEB}/api/v1/alerts", headers=viewer)).status_code != 200:
            fail("viewer could not read alerts")
        r = await anon.post(f"{WEB}/api/v1/auth/logout-all", headers=viewer)
        if r.status_code != 204:
            fail(f"log out everywhere failed: {r.status_code} {r.text}")
        if (await anon.get(f"{WEB}/api/v1/auth/me", headers=viewer)).status_code != 401:
            fail("session still valid after logging out everywhere")
        fault = {"kind": "spike", "deviceId": "sensor-001"}
        if (await anon.post(f"{SIM}/faults", json=fault)).status_code != 401:
            fail("simulator accepted a fault without its token")
    log("auth: anonymous requests refused, new viewer can read but not change rules")


async def post_fault(http: httpx.AsyncClient, **body) -> None:
    r = await http.post(f"{SIM}/faults", json=body, headers=SIM_AUTH)
    if r.status_code != 202:
        fail(f"simulator rejected fault {body}: {r.status_code} {r.text}")


async def main() -> None:
    async with httpx.AsyncClient(timeout=10) as probe:
        log("waiting for services")
        for name, url in (("simulator", f"{SIM}/health"), ("web", f"{WEB}/healthz")):
            await wait_until(name, lambda url=url: _is_up(probe, url), 90)
    # succeeds once the analysis service is up and the bootstrap admin exists
    token = await wait_until("admin login", lambda: login(*ADMIN), 90)
    session = {"Cookie": f"session={token}"}
    await check_auth()
    await check_headers()

    async with httpx.AsyncClient(timeout=10, headers=session) as http:
        audit = await http.get(f"{WEB}/api/v1/audit", params={"action": "login.success"})
        if audit.status_code != 200 or not any(
            e["actorUsername"] == ADMIN[0] for e in audit.json()["items"]
        ):
            fail(f"admin login missing from the audit log: {audit.status_code} {audit.text}")
        log("audit log records logins")
        rules = (await http.get(f"{WEB}/api/v1/rules")).json()
        if not rules:
            fail("no rules seeded")

        # let the pipeline warm up so a baseline exists
        await asyncio.sleep(5)
        before = await alerts(http, deviceId="sensor-001", state="open")
        known_ids = {a["id"] for a in before}

        async with websockets.connect(WS, additional_headers=session) as ws:
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
