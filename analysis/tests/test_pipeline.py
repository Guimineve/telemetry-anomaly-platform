from datetime import UTC, datetime

from analysis.pipeline import AlertPipeline
from analysis.rules.models import Alert


def alert(i):
    return Alert(
        f"a{i}",
        "r",
        "rule",
        "threshold",
        "d",
        "temperature",
        "info",
        "open",
        "m",
        1.0,
        datetime(2026, 1, 1, tzinfo=UTC),
    )


class FlakySink:
    def __init__(self, fail_times=0):
        self.fail_times, self.saved, self.calls = fail_times, [], 0

    async def save_alerts(self, alerts):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("db down")
        self.saved.extend(alerts)


class FlakyPublisher:
    def __init__(self, fail_times=0):
        self.fail_times, self.sent, self.calls = fail_times, [], 0

    async def publish(self, a):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("kafka down")
        self.sent.append(a.id)


async def no_sleep(_):
    return None


async def test_happy_path_persists_then_publishes_in_order():
    sink, pub = FlakySink(), FlakyPublisher()
    await AlertPipeline(sink, pub, no_sleep).emit([alert(1), alert(2)])
    assert [a.id for a in sink.saved] == ["a1", "a2"]
    assert pub.sent == ["a1", "a2"]


async def test_empty_batch_does_nothing():
    sink, pub = FlakySink(), FlakyPublisher()
    await AlertPipeline(sink, pub, no_sleep).emit([])
    assert sink.calls == 0 and pub.calls == 0


async def test_database_outage_is_retried_not_dropped():
    sink, pub = FlakySink(fail_times=3), FlakyPublisher()
    await AlertPipeline(sink, pub, no_sleep).emit([alert(1)])
    assert sink.calls == 4 and len(sink.saved) == 1 and pub.sent == ["a1"]


async def test_publish_failure_does_not_reinsert_rows():
    sink, pub = FlakySink(), FlakyPublisher(fail_times=2)
    await AlertPipeline(sink, pub, no_sleep).emit([alert(1)])
    assert sink.calls == 1  # persisted exactly once
    assert pub.sent == ["a1"]


async def test_backoff_grows_and_is_capped():
    delays = []

    async def record(d):
        delays.append(d)

    await AlertPipeline(FlakySink(fail_times=6), FlakyPublisher(), record, max_backoff_s=3).emit(
        [alert(1)]
    )
    assert delays == [0.5, 1.0, 2.0, 3.0, 3.0, 3.0]
