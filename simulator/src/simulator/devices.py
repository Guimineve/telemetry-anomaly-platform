"""Pure telemetry generation with fault injection.

No I/O and no hidden clock: time and randomness are injected, so behaviour is
reproducible in tests.
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

FaultKind = Literal["drift", "spike", "dropout", "malformed"]

# metric -> (unit, nominal value, noise standard deviation)
METRICS: dict[str, tuple[str, float, float]] = {
    "temperature": ("celsius", 40.0, 0.4),
    "voltage": ("volt", 28.0, 0.05),
    "pressure": ("kPa", 101.0, 0.3),
}


@dataclass(frozen=True)
class Outgoing:
    """One message ready to publish. `valid` is False for injected bad payloads."""

    key: str
    value: bytes
    valid: bool = True


@dataclass
class Fault:
    kind: FaultKind
    device_id: str
    metric: str = "temperature"
    magnitude: float = 10.0  # drift: total ramp; spike: added value
    duration_s: float = 30.0  # drift ramp length / dropout length
    count: int = 1  # malformed: number of bad messages
    started_at: datetime | None = field(default=None, compare=False)


def format_ts(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _bad_payloads(device_id: str, ts: datetime, rng: random.Random) -> list[bytes]:
    good = {
        "id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "deviceId": device_id,
        "metric": "temperature",
        "value": 1.0,
        "unit": "celsius",
        "timestamp": format_ts(ts),
    }
    broken = [
        b"not json at all {",
        json.dumps({k: v for k, v in good.items() if k != "value"}).encode(),
        json.dumps({**good, "value": "hot"}).encode(),
        json.dumps({**good, "metric": "unknown-metric"}).encode(),
        json.dumps({**good, "extra": 1}).encode(),
    ]
    return broken


class Simulator:
    def __init__(self, device_ids: list[str], rng: random.Random | None = None) -> None:
        self.device_ids = list(device_ids)
        self._rng = rng or random.Random()
        self._heartbeat: dict[str, int] = {d: 0 for d in self.device_ids}
        self._faults: list[Fault] = []

    # -- control ---------------------------------------------------------
    def inject(self, fault: Fault, now: datetime) -> Fault:
        if fault.device_id not in self.device_ids:
            raise KeyError(fault.device_id)
        if fault.kind in ("drift", "spike") and fault.metric not in METRICS:
            raise ValueError(f"unknown metric {fault.metric!r}")
        if fault.kind in ("drift", "dropout") and fault.duration_s <= 0:
            raise ValueError("duration_s must be positive")
        fault.started_at = now
        self._faults.append(fault)
        return fault

    def active_faults(self) -> list[Fault]:
        return list(self._faults)

    # -- generation ------------------------------------------------------
    def tick(self, now: datetime) -> list[Outgoing]:
        out: list[Outgoing] = []
        self._expire(now)
        for device in self.device_ids:
            out.extend(self._tick_device(device, now))
        return out

    def _expire(self, now: datetime) -> None:
        def alive(f: Fault) -> bool:
            assert f.started_at is not None
            if f.kind in ("spike", "malformed"):
                return True  # consumed on the next tick
            return now < f.started_at + timedelta(seconds=f.duration_s)

        self._faults = [f for f in self._faults if alive(f)]

    def _tick_device(self, device: str, now: datetime) -> list[Outgoing]:
        mine = [f for f in self._faults if f.device_id == device]
        if any(f.kind == "dropout" for f in mine):
            return []

        out: list[Outgoing] = []
        for metric, (unit, nominal, sigma) in METRICS.items():
            value = nominal + self._rng.gauss(0.0, sigma)
            for f in mine:
                if f.metric != metric:
                    continue
                if f.kind == "drift":
                    assert f.started_at is not None
                    elapsed = (now - f.started_at).total_seconds()
                    value += f.magnitude * min(elapsed / f.duration_s, 1.0)
                elif f.kind == "spike":
                    value += f.magnitude
            out.append(self._reading(device, metric, round(value, 3), unit, now))

        self._heartbeat[device] += 1
        out.append(self._reading(device, "heartbeat", self._heartbeat[device], "count", now))

        for f in mine:
            if f.kind == "malformed":
                bad = _bad_payloads(device, now, self._rng)
                for i in range(f.count):
                    out.append(Outgoing(device, bad[i % len(bad)], valid=False))

        # one-shot faults are consumed once applied
        one_shot = ("spike", "malformed")
        self._faults = [
            f for f in self._faults if not (f.device_id == device and f.kind in one_shot)
        ]
        return out

    def _reading(
        self, device: str, metric: str, value: float, unit: str, now: datetime
    ) -> Outgoing:
        payload = {
            "id": str(uuid.UUID(int=self._rng.getrandbits(128), version=4)),
            "deviceId": device,
            "metric": metric,
            "value": value,
            "unit": unit,
            "timestamp": format_ts(now),
        }
        return Outgoing(device, json.dumps(payload).encode())
