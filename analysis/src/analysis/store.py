"""Postgres access for rules and alerts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .db import AlertRow, RuleRow
from .rules.models import Alert, Rule

_NS = uuid.UUID("6f1d0a52-6b0e-4f5e-9d3a-0a1b2c3d4e5f")

DEFAULT_RULES: list[dict] = [
    dict(
        name="Temperature too high",
        type="threshold",
        metric="temperature",
        params={"max": 60},
        severity="critical",
    ),
    dict(
        name="Voltage out of range",
        type="threshold",
        metric="voltage",
        params={"min": 24, "max": 32},
        severity="warning",
    ),
    dict(
        name="Pressure out of range",
        type="threshold",
        metric="pressure",
        params={"min": 90, "max": 115},
        severity="warning",
    ),
    dict(
        name="Temperature changing fast",
        type="rate_of_change",
        metric="temperature",
        params={"max_rate_per_s": 8, "window_s": 5},
        severity="warning",
    ),
    dict(
        name="Device silent",
        type="missing_heartbeat",
        metric=None,
        params={"timeout_s": 10},
        severity="critical",
    ),
    dict(
        name="Statistical temperature anomaly",
        type="zscore",
        metric="temperature",
        params={"window": 60, "threshold": 4.0, "min_samples": 30},
        severity="info",
    ),
]


def row_to_rule(r: RuleRow) -> Rule:
    return Rule(
        id=r.id,
        name=r.name,
        type=r.type,  # type: ignore[arg-type]
        metric=r.metric,
        device_pattern=r.device_pattern,
        params=dict(r.params or {}),
        severity=r.severity,  # type: ignore[arg-type]
        enabled=r.enabled,
    )


class Store:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self._sessions = sessions

    async def load_rules(self) -> list[Rule]:
        async with self._sessions() as s:
            rows = (await s.execute(select(RuleRow))).scalars().all()
        return [row_to_rule(r) for r in rows]

    async def seed_default_rules(self) -> int:
        """Insert the default rules if the table is empty. Returns how many were inserted."""
        async with self._sessions.begin() as s:
            count = (await s.execute(select(func.count()).select_from(RuleRow))).scalar_one()
            if count:
                return 0
            for spec in DEFAULT_RULES:
                s.add(RuleRow(id=str(uuid.uuid5(_NS, spec["name"])), **spec))
            return len(DEFAULT_RULES)

    async def save_alerts(self, alerts: Sequence[Alert]) -> None:
        async with self._sessions.begin() as s:
            s.add_all(
                AlertRow(
                    id=a.id,
                    rule_id=a.rule_id,
                    rule_name=a.rule_name,
                    rule_type=a.rule_type,
                    device_id=a.device_id,
                    metric=a.metric,
                    severity=a.severity,
                    state=a.state,
                    message=a.message,
                    value=a.value,
                    ts=a.timestamp,
                )
                for a in alerts
            )

    async def open_alert_keys(self) -> list[tuple[str, str]]:
        """(rule_id, device_id) pairs whose latest alert event is still `open`."""
        stmt = (
            select(AlertRow.rule_id, AlertRow.device_id, AlertRow.state)
            .distinct(AlertRow.rule_id, AlertRow.device_id)
            .order_by(
                AlertRow.rule_id, AlertRow.device_id, AlertRow.ts.desc(), AlertRow.created_at.desc()
            )
        )
        async with self._sessions() as s:
            rows = (await s.execute(stmt)).all()
        return [(r.rule_id, r.device_id) for r in rows if r.state == "open"]

    # -- REST API queries ------------------------------------------------
    async def list_rules(self) -> list[RuleRow]:
        async with self._sessions() as s:
            return list((await s.execute(select(RuleRow).order_by(RuleRow.name))).scalars().all())

    async def get_rule(self, rule_id: str) -> RuleRow | None:
        async with self._sessions() as s:
            return await s.get(RuleRow, rule_id)

    async def create_rule(self, data: dict) -> RuleRow:
        async with self._sessions.begin() as s:
            row = RuleRow(id=str(uuid.uuid4()), **data)
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def update_rule(self, rule_id: str, data: dict) -> RuleRow | None:
        async with self._sessions.begin() as s:
            row = await s.get(RuleRow, rule_id)
            if row is None:
                return None
            for key, value in data.items():
                setattr(row, key, value)
            await s.flush()
            await s.refresh(row)
            return row

    async def delete_rule(self, rule_id: str) -> bool:
        async with self._sessions.begin() as s:
            row = await s.get(RuleRow, rule_id)
            if row is None:
                return False
            await s.delete(row)
            return True

    async def list_alerts(
        self,
        *,
        device_id: str | None = None,
        severity: str | None = None,
        state: str | None = None,
        rule_id: str | None = None,
        acknowledged: bool | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AlertRow], int]:
        conds = []
        if device_id:
            conds.append(AlertRow.device_id == device_id)
        if severity:
            conds.append(AlertRow.severity == severity)
        if state:
            conds.append(AlertRow.state == state)
        if rule_id:
            conds.append(AlertRow.rule_id == rule_id)
        if acknowledged is True:
            conds.append(AlertRow.acknowledged_at.is_not(None))
        elif acknowledged is False:
            conds.append(AlertRow.acknowledged_at.is_(None))
        if since:
            conds.append(AlertRow.ts >= since)
        if until:
            conds.append(AlertRow.ts <= until)
        async with self._sessions() as s:
            total = (
                await s.execute(select(func.count()).select_from(AlertRow).where(*conds))
            ).scalar_one()
            rows = (
                (
                    await s.execute(
                        select(AlertRow)
                        .where(*conds)
                        .order_by(AlertRow.ts.desc(), AlertRow.id)
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
        return list(rows), total

    async def get_alert(self, alert_id: str) -> AlertRow | None:
        async with self._sessions() as s:
            return await s.get(AlertRow, alert_id)

    async def ack_alert(self, alert_id: str, now: datetime) -> AlertRow | None:
        async with self._sessions.begin() as s:
            row = await s.get(AlertRow, alert_id)
            if row is None:
                return None
            if row.acknowledged_at is None:
                row.acknowledged_at = now
            return row
