"""Conversions between Kafka JSON, domain objects and rows."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from .rules.models import Alert, Reading

log = logging.getLogger("analysis.serialization")


def parse_reading(raw: bytes | None) -> Reading | None:
    """Parse a validated reading. Ingestion already validated it, so failures are only logged."""
    if raw is None:
        return None
    try:
        d = json.loads(raw)
        ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
        return Reading(
            id=d["id"],
            device_id=d["deviceId"],
            metric=d["metric"],
            value=float(d["value"]),
            timestamp=ts.astimezone(UTC),
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        log.warning("dropping unparsable reading: %s", exc)
        return None


def format_ts(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def alert_to_dict(a: Alert) -> dict[str, Any]:
    """Shape defined by contracts/alert.schema.json."""
    return {
        "id": a.id,
        "ruleId": a.rule_id,
        "ruleName": a.rule_name,
        "ruleType": a.rule_type,
        "deviceId": a.device_id,
        "metric": a.metric,
        "severity": a.severity,
        "state": a.state,
        "message": a.message,
        "value": a.value,
        "timestamp": format_ts(a.timestamp),
    }
