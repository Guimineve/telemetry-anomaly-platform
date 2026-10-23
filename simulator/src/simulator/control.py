"""Small HTTP control surface to trigger faults and inspect the simulator.

Everything but /health needs `Authorization: Bearer <token>`. With no token configured the
control endpoints are refused outright rather than left open.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .devices import Fault, Simulator


@dataclass
class Stats:
    sent: int = 0
    failed: int = 0


class FaultRequest(BaseModel):
    kind: Literal["drift", "spike", "dropout", "malformed"]
    device_id: str = Field(alias="deviceId")
    metric: str = "temperature"
    magnitude: float = 10.0
    duration_s: float = Field(default=30.0, alias="durationS")
    count: int = Field(default=1, ge=1, le=1000)

    model_config = {"populate_by_name": True}


def create_app(sim: Simulator, stats: Stats, token: str = "") -> FastAPI:
    app = FastAPI(title="Telemetry simulator control", version="0.1.0")

    def authorized(authorization: str = Header(default="")) -> None:
        if not token:
            raise HTTPException(503, "control API disabled: SIMULATOR_TOKEN is not set")
        scheme, _, given = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "missing or wrong bearer token")

    guarded = [Depends(authorized)]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/devices", dependencies=guarded)
    def devices() -> dict[str, object]:
        return {
            "devices": sim.device_ids,
            "activeFaults": [
                {"kind": f.kind, "deviceId": f.device_id, "metric": f.metric}
                for f in sim.active_faults()
            ],
            "sent": stats.sent,
            "failed": stats.failed,
        }

    @app.post("/faults", status_code=202, dependencies=guarded)
    def inject(req: FaultRequest) -> dict[str, str]:
        fault = Fault(
            kind=req.kind,
            device_id=req.device_id,
            metric=req.metric,
            magnitude=req.magnitude,
            duration_s=req.duration_s,
            count=req.count,
        )
        try:
            sim.inject(fault, datetime.now(UTC))
        except KeyError:
            raise HTTPException(404, f"unknown device {req.device_id!r}") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"status": "accepted", "kind": req.kind, "deviceId": req.device_id}

    return app
