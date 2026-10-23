"""In-memory stand-in for Store, used to test the HTTP layer without a database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from analysis.db import AlertRow, RuleRow, UserRow


class FakeStore:
    def __init__(self) -> None:
        self.rules: dict[str, RuleRow] = {}
        self.alerts: dict[str, AlertRow] = {}
        self.users: dict[str, UserRow] = {}

    async def list_rules(self):
        return sorted(self.rules.values(), key=lambda r: r.name)

    async def get_rule(self, rule_id):
        return self.rules.get(rule_id)

    async def create_rule(self, data):
        now = datetime.now(UTC)
        row = RuleRow(id=str(uuid.uuid4()), created_at=now, updated_at=now, **data)
        self.rules[row.id] = row
        return row

    async def update_rule(self, rule_id, data):
        row = self.rules.get(rule_id)
        if row is None:
            return None
        for k, v in data.items():
            setattr(row, k, v)
        row.updated_at = datetime.now(UTC)
        return row

    async def delete_rule(self, rule_id):
        return self.rules.pop(rule_id, None) is not None

    def add_alert(self, **kw) -> AlertRow:
        row = AlertRow(
            **{
                "id": uuid.uuid4().hex,
                "rule_id": "r1",
                "rule_name": "temp high",
                "rule_type": "threshold",
                "device_id": "sensor-001",
                "metric": "temperature",
                "severity": "critical",
                "state": "open",
                "message": "too hot",
                "value": 90.0,
                "ts": datetime(2026, 1, 1, tzinfo=UTC),
                "acknowledged_at": None,
                **kw,
            }
        )
        self.alerts[row.id] = row
        return row

    async def list_alerts(
        self, *, limit=50, offset=0, acknowledged=None, since=None, until=None, **eq
    ):
        rows = list(self.alerts.values())
        for key, value in eq.items():
            if value is not None:
                rows = [r for r in rows if getattr(r, key) == value]
        if acknowledged is not None:
            rows = [r for r in rows if (r.acknowledged_at is not None) == acknowledged]
        if since:
            rows = [r for r in rows if r.ts >= since]
        if until:
            rows = [r for r in rows if r.ts <= until]
        rows.sort(key=lambda r: r.ts, reverse=True)
        return rows[offset : offset + limit], len(rows)

    async def get_alert(self, alert_id):
        return self.alerts.get(alert_id)

    async def ack_alert(self, alert_id, now):
        row = self.alerts.get(alert_id)
        if row is not None and row.acknowledged_at is None:
            row.acknowledged_at = now
        return row

    async def create_user(self, username, password_hash, role):
        if any(u.username == username for u in self.users.values()):
            return None
        row = UserRow(
            id=str(uuid.uuid4()),
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=True,
            created_at=datetime.now(UTC),
            last_login_at=None,
        )
        self.users[row.id] = row
        return row

    async def get_user(self, user_id):
        return self.users.get(user_id)

    async def get_user_by_username(self, username):
        return next((u for u in self.users.values() if u.username == username), None)

    async def list_users(self):
        return sorted(self.users.values(), key=lambda u: u.username)

    async def update_user(self, user_id, data):
        row = self.users.get(user_id)
        if row is not None:
            for k, v in data.items():
                setattr(row, k, v)
        return row

    async def record_login(self, user_id, now):
        if user_id in self.users:
            self.users[user_id].last_login_at = now
