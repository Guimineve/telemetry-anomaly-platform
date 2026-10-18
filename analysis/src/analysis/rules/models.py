"""Plain domain types for the rules engine. No I/O, no framework imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

RuleType = Literal["threshold", "rate_of_change", "missing_heartbeat", "zscore"]
Severity = Literal["info", "warning", "critical"]
AlertState = Literal["open", "resolved"]

RULE_TYPES: tuple[str, ...] = ("threshold", "rate_of_change", "missing_heartbeat", "zscore")


@dataclass(frozen=True)
class Reading:
    id: str
    device_id: str
    metric: str
    value: float
    timestamp: datetime


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    type: RuleType
    metric: str | None  # None for missing_heartbeat means the "heartbeat" metric
    device_pattern: str = "*"
    params: dict[str, Any] = field(default_factory=dict)
    severity: Severity = "warning"
    enabled: bool = True


@dataclass(frozen=True)
class Alert:
    id: str
    rule_id: str
    rule_name: str
    rule_type: RuleType
    device_id: str
    metric: str | None
    severity: Severity
    state: AlertState
    message: str
    value: float | None
    timestamp: datetime
