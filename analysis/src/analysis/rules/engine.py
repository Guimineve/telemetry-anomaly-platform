"""Pure rules engine.

Time and ids are injected so behaviour is deterministic in tests. The engine keeps one piece of
state per (rule, device): whether an alert is currently *open*. It emits an `open` alert when a
condition starts being violated and a `resolved` alert when it stops, so a sustained breach
produces one alert, not one per reading.

Missing-heartbeat detection uses the *arrival* time of heartbeats (the injected clock), not the
device timestamp, so it measures "has the pipeline heard from this device recently".
"""

from __future__ import annotations

import math
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from fnmatch import fnmatchcase

from .models import Alert, AlertState, Reading, Rule

Clock = Callable[[], datetime]
Key = tuple[str, str]  # (rule id, device id)

DEFAULT_HEARTBEAT_METRIC = "heartbeat"
_EPS = 1e-9


class RulesEngine:
    def __init__(
        self,
        clock: Clock,
        cooldown_s: float = 0.0,
        startup_grace_s: float = 0.0,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._clock = clock
        self._cooldown = timedelta(seconds=cooldown_s)
        self._grace = timedelta(seconds=startup_grace_s)
        self._new_id = id_factory
        self._started_at = clock()
        self._rules: dict[str, Rule] = {}
        self._open: set[Key] = set()
        self._last_opened: dict[Key, datetime] = {}
        self._history: dict[Key, deque[tuple[datetime, float]]] = {}
        self._last_seen: dict[str, datetime] = {}  # device -> arrival time of last heartbeat

    # -- configuration ---------------------------------------------------
    def set_rules(self, rules: Iterable[Rule]) -> None:
        """Replace the rule set, keeping state for rules that still exist."""
        self._rules = {r.id: r for r in rules}
        self._open = {k for k in self._open if k[0] in self._rules}
        self._history = {k: v for k, v in self._history.items() if k[0] in self._rules}
        self._last_opened = {k: v for k, v in self._last_opened.items() if k[0] in self._rules}

    def seed_open(self, rule_id: str, device_id: str) -> None:
        """Mark an alert as already open (restored from storage after a restart)."""
        self._open.add((rule_id, device_id))

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules.values())

    # -- evaluation ------------------------------------------------------
    def on_reading(self, reading: Reading) -> list[Alert]:
        alerts: list[Alert] = []
        if reading.metric == DEFAULT_HEARTBEAT_METRIC:
            self._last_seen[reading.device_id] = self._clock()
        for rule in self._rules.values():
            if not rule.enabled or not fnmatchcase(reading.device_id, rule.device_pattern):
                continue
            if rule.type == "missing_heartbeat":
                if (rule.metric or DEFAULT_HEARTBEAT_METRIC) == reading.metric:
                    alerts += self._transition(
                        rule,
                        reading.device_id,
                        None,
                        False,
                        "heartbeat received",
                        None,
                        reading.timestamp,
                    )
                continue
            if rule.metric != reading.metric:
                continue
            violated, message = self._evaluate(rule, reading)
            alerts += self._transition(
                rule,
                reading.device_id,
                reading.metric,
                violated,
                message,
                reading.value,
                reading.timestamp,
            )
        return alerts

    def sweep(self) -> list[Alert]:
        """Check for silent devices. Call periodically."""
        now = self._clock()
        if now - self._started_at < self._grace:
            return []
        alerts: list[Alert] = []
        for rule in self._rules.values():
            if rule.type != "missing_heartbeat" or not rule.enabled:
                continue
            timeout = timedelta(seconds=float(rule.params.get("timeout_s", 30)))
            for device, seen in self._last_seen.items():
                if not fnmatchcase(device, rule.device_pattern):
                    continue
                silent_for = now - seen
                if silent_for > timeout:
                    msg = f"no heartbeat from {device} for {silent_for.total_seconds():.0f}s"
                    alerts += self._transition(rule, device, None, True, msg, None, now)
        return alerts

    # -- rule types ------------------------------------------------------
    def _evaluate(self, rule: Rule, r: Reading) -> tuple[bool, str]:
        if rule.type == "threshold":
            return self._threshold(rule, r)
        if rule.type == "rate_of_change":
            return self._rate_of_change(rule, r)
        if rule.type == "zscore":
            return self._zscore(rule, r)
        return False, ""

    @staticmethod
    def _threshold(rule: Rule, r: Reading) -> tuple[bool, str]:
        lo = rule.params.get("min")
        hi = rule.params.get("max")
        if hi is not None and r.value > hi:
            return True, f"{r.metric} {r.value:g} above max {hi:g}"
        if lo is not None and r.value < lo:
            return True, f"{r.metric} {r.value:g} below min {lo:g}"
        return False, f"{r.metric} back within range ({r.value:g})"

    def _rate_of_change(self, rule: Rule, r: Reading) -> tuple[bool, str]:
        window = timedelta(seconds=float(rule.params.get("window_s", 10)))
        max_rate = float(rule.params["max_rate_per_s"])
        hist = self._history.setdefault((rule.id, r.device_id), deque())
        hist.append((r.timestamp, r.value))
        while hist and r.timestamp - hist[0][0] > window:
            hist.popleft()
        oldest_ts, oldest_val = hist[0]
        dt = (r.timestamp - oldest_ts).total_seconds()
        if dt <= 0:
            return False, ""
        rate = (r.value - oldest_val) / dt
        if abs(rate) > max_rate:
            return True, f"{r.metric} changing at {rate:+.2f}/s, limit {max_rate:g}/s"
        return False, f"{r.metric} rate back to normal ({rate:+.2f}/s)"

    def _zscore(self, rule: Rule, r: Reading) -> tuple[bool, str]:
        size = int(rule.params.get("window", 60))
        limit = float(rule.params.get("threshold", 3.0))
        min_samples = int(rule.params.get("min_samples", 20))
        hist = self._history.setdefault((rule.id, r.device_id), deque(maxlen=size))
        prior = [v for _, v in hist]
        hist.append((r.timestamp, r.value))  # the window adapts to sustained shifts
        if len(prior) < min_samples:
            return False, ""
        mean = sum(prior) / len(prior)
        std = math.sqrt(sum((v - mean) ** 2 for v in prior) / len(prior))
        if std < _EPS:
            return False, ""
        z = (r.value - mean) / std
        if abs(z) > limit:
            return True, f"{r.metric} {r.value:g} is {z:+.1f} sigma from recent mean {mean:.2f}"
        return False, f"{r.metric} back within {limit:g} sigma ({z:+.1f})"

    # -- state machine ---------------------------------------------------
    def _transition(
        self,
        rule: Rule,
        device: str,
        metric: str | None,
        violated: bool,
        message: str,
        value: float | None,
        ts: datetime,
    ) -> list[Alert]:
        key: Key = (rule.id, device)
        if violated and key not in self._open:
            last = self._last_opened.get(key)
            if last is not None and self._clock() - last < self._cooldown:
                return []  # flapping: stay quiet during the cooldown
            self._open.add(key)
            self._last_opened[key] = self._clock()
            return [self._alert(rule, device, metric, "open", message, value, ts)]
        if not violated and key in self._open:
            self._open.discard(key)
            return [self._alert(rule, device, metric, "resolved", message, value, ts)]
        return []

    def _alert(
        self,
        rule: Rule,
        device: str,
        metric: str | None,
        state: AlertState,
        message: str,
        value: float | None,
        ts: datetime,
    ) -> Alert:
        return Alert(
            id=self._new_id(),
            rule_id=rule.id,
            rule_name=rule.name,
            rule_type=rule.type,
            device_id=device,
            metric=metric,
            severity=rule.severity,
            state=state,
            message=message,
            value=value,
            timestamp=ts,
        )
