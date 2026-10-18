from datetime import UTC, datetime, timedelta

import pytest

from analysis.rules.engine import RulesEngine
from analysis.rules.models import Reading, Rule

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def reading(value, metric="temperature", device="sensor-001", at=0.0, rid="r"):
    return Reading(rid, device, metric, value, T0 + timedelta(seconds=at))


def engine(*rules, clock=None, **kw):
    e = RulesEngine(clock or FakeClock(), **kw)
    e.set_rules(rules)
    return e


THRESH = Rule(
    "t1",
    "temp high",
    "threshold",
    "temperature",
    params={"max": 60, "min": 10},
    severity="critical",
)


# -- threshold -----------------------------------------------------------
def test_threshold_in_range_emits_nothing():
    assert engine(THRESH).on_reading(reading(40)) == []


@pytest.mark.parametrize("value", [60.0, 10.0])
def test_threshold_boundaries_are_inclusive(value):
    assert engine(THRESH).on_reading(reading(value)) == []


@pytest.mark.parametrize("value,word", [(60.01, "above"), (9.99, "below")])
def test_threshold_breach_opens_alert(value, word):
    (a,) = engine(THRESH).on_reading(reading(value))
    assert a.state == "open" and a.severity == "critical" and a.rule_id == "t1"
    assert a.device_id == "sensor-001" and a.value == value and word in a.message


def test_sustained_breach_alerts_once_then_resolves():
    e = engine(THRESH)
    assert len(e.on_reading(reading(90, at=0))) == 1
    assert e.on_reading(reading(91, at=1)) == []
    assert e.on_reading(reading(92, at=2)) == []
    (resolved,) = e.on_reading(reading(40, at=3))
    assert resolved.state == "resolved"
    assert e.on_reading(reading(41, at=4)) == []


def test_state_is_per_device():
    e = engine(THRESH)
    assert len(e.on_reading(reading(90, device="sensor-001"))) == 1
    assert len(e.on_reading(reading(90, device="sensor-002"))) == 1


def test_rule_only_applies_to_its_metric_and_device_pattern():
    r = Rule(
        "t2", "volt", "threshold", "voltage", device_pattern="sensor-00[12]", params={"max": 30}
    )
    e = engine(r)
    assert e.on_reading(reading(99, metric="temperature")) == []
    assert e.on_reading(reading(99, metric="voltage", device="sensor-009")) == []
    assert len(e.on_reading(reading(99, metric="voltage", device="sensor-002"))) == 1


def test_disabled_rule_is_ignored():
    r = Rule("t3", "off", "threshold", "temperature", params={"max": 1}, enabled=False)
    assert engine(r).on_reading(reading(50)) == []


def test_cooldown_suppresses_reopen_but_not_after():
    clock = FakeClock()
    e = engine(THRESH, clock=clock, cooldown_s=30)
    assert len(e.on_reading(reading(90))) == 1
    assert e.on_reading(reading(40))[0].state == "resolved"
    clock.advance(10)
    assert e.on_reading(reading(90)) == []  # flapping, within cooldown
    clock.advance(30)
    assert len(e.on_reading(reading(90))) == 1


def test_seed_open_prevents_duplicate_after_restart():
    e = engine(THRESH)
    e.seed_open("t1", "sensor-001")
    assert e.on_reading(reading(90)) == []
    assert e.on_reading(reading(40))[0].state == "resolved"


def test_set_rules_keeps_state_for_surviving_rules_and_drops_removed():
    e = engine(THRESH)
    e.on_reading(reading(90))
    e.set_rules([THRESH])
    assert e.on_reading(reading(91)) == []  # still open
    e.set_rules([])
    e.set_rules([THRESH])
    assert len(e.on_reading(reading(91))) == 1  # state was dropped with the rule


# -- rate of change ------------------------------------------------------
ROC = Rule(
    "r1",
    "fast change",
    "rate_of_change",
    "temperature",
    params={"max_rate_per_s": 2.0, "window_s": 10},
)


def test_rate_of_change_flags_fast_rise_and_resolves():
    e = engine(ROC)
    assert e.on_reading(reading(40, at=0)) == []
    (a,) = e.on_reading(reading(50, at=1))  # +10/s
    assert a.state == "open" and "+10.00/s" in a.message
    assert e.on_reading(reading(50.1, at=20))[0].state == "resolved"  # old samples aged out


def test_rate_of_change_flags_fast_fall():
    e = engine(ROC)
    e.on_reading(reading(40, at=0))
    (a,) = e.on_reading(reading(30, at=1))
    assert a.state == "open" and "-10.00/s" in a.message


def test_rate_of_change_slow_drift_is_fine():
    e = engine(ROC)
    for i in range(10):
        assert e.on_reading(reading(40 + 0.5 * i, at=i)) == []


def test_rate_of_change_single_sample_is_fine():
    assert engine(ROC).on_reading(reading(1000, at=0)) == []


# -- z-score -------------------------------------------------------------
ZS = Rule(
    "z1",
    "stat",
    "zscore",
    "temperature",
    params={"window": 30, "threshold": 3.0, "min_samples": 10},
)


def feed_normal(e, n=20):
    for i in range(n):
        assert e.on_reading(reading(40 + (0.1 if i % 2 else -0.1), at=i)) == []


def test_zscore_waits_for_enough_samples():
    e = engine(ZS)
    for i in range(5):
        e.on_reading(reading(40 + (0.1 if i % 2 else -0.1), at=i))
    assert e.on_reading(reading(500, at=5)) == []


def test_zscore_flags_outlier_and_resolves():
    e = engine(ZS)
    feed_normal(e)
    (a,) = e.on_reading(reading(45, at=21))
    assert a.state == "open" and "sigma" in a.message
    assert e.on_reading(reading(40.0, at=22))[0].state == "resolved"


def test_zscore_ignores_normal_noise():
    e = engine(ZS)
    feed_normal(e, 40)


def test_zscore_constant_history_makes_no_judgement():
    e = engine(ZS)
    for i in range(15):
        e.on_reading(reading(40.0, at=i))
    assert e.on_reading(reading(41.0, at=16)) == []  # std == 0 -> undefined, stay quiet


# -- missing heartbeat ---------------------------------------------------
HB = Rule("h1", "silent", "missing_heartbeat", None, params={"timeout_s": 10}, severity="critical")


def hb(device="sensor-001", at=0.0, v=1):
    return reading(v, metric="heartbeat", device=device, at=at)


def test_missing_heartbeat_fires_after_timeout_then_resolves_on_return():
    clock = FakeClock()
    e = engine(HB, clock=clock)
    assert e.on_reading(hb()) == []
    clock.advance(10)
    assert e.sweep() == []  # exactly at the timeout is not yet silent
    clock.advance(1)
    (a,) = e.sweep()
    assert a.state == "open" and a.device_id == "sensor-001" and a.rule_type == "missing_heartbeat"
    clock.advance(5)
    assert e.sweep() == []  # still open, no spam
    (r,) = e.on_reading(hb(v=2))
    assert r.state == "resolved"


def test_missing_heartbeat_only_flags_the_silent_device():
    clock = FakeClock()
    e = engine(HB, clock=clock)
    e.on_reading(hb("sensor-001"))
    e.on_reading(hb("sensor-002"))
    clock.advance(8)
    e.on_reading(hb("sensor-002", v=2))
    clock.advance(5)
    alerts = e.sweep()
    assert [a.device_id for a in alerts] == ["sensor-001"]


def test_missing_heartbeat_startup_grace_suppresses_early_alerts():
    clock = FakeClock()
    e = engine(HB, clock=clock, startup_grace_s=60)
    e.on_reading(hb())
    clock.advance(30)
    assert e.sweep() == []  # silent for 30s but still inside grace
    clock.advance(31)
    assert len(e.sweep()) == 1


def test_never_seen_device_cannot_be_flagged():
    clock = FakeClock()
    e = engine(HB, clock=clock)
    clock.advance(1000)
    assert e.sweep() == []  # documented limitation


def test_heartbeat_rule_and_metric_rules_do_not_cross_talk():
    e = engine(HB, THRESH, clock=FakeClock())
    assert e.on_reading(hb()) == []  # a heartbeat never trips the temperature threshold
    (a,) = e.on_reading(reading(5, metric="temperature"))  # below min=10
    assert a.rule_id == "t1"
