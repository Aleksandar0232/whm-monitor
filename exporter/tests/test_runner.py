from conftest import exposition, value
from prometheus_client.core import GaugeMetricFamily

from whm_exporter.collectors.base import Collector
from whm_exporter.runner import CachedMetrics, CollectorRunner


class FlakyCollector(Collector):
    name = "flaky"

    def __init__(self):
        self.fail = False
        self.calls = 0

    def collect(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("WHM is down")
        fam = GaugeMetricFamily("whm_test_value", "test")
        fam.add_metric([], self.calls)
        return [fam]


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(interval=60, max_age_intervals=3):
    clock = Clock()
    collector = FlakyCollector()
    runner = CollectorRunner(collector, interval, max_age_intervals, clock=clock)
    return collector, runner, CachedMetrics([runner], clock=clock), clock


def fams(cached):
    return list(cached.collect())


def test_nothing_served_before_first_run():
    _, _, cached, _ = make()
    out = fams(cached)
    assert value(out, "whm_collector_up", collector="flaky") is None
    assert value(out, "whm_collector_interval_seconds", collector="flaky") == 60
    assert value(out, "whm_test_value") is None


def test_success_serves_data():
    _, runner, cached, clock = make()
    assert runner.run_once()
    out = fams(cached)
    assert value(out, "whm_collector_up", collector="flaky") == 1
    assert value(out, "whm_collector_stale", collector="flaky") == 0
    assert value(out, "whm_collector_last_success_timestamp_seconds", collector="flaky") == clock.t
    assert value(out, "whm_test_value") == 1


def test_failure_keeps_last_good_data_until_max_age():
    collector, runner, cached, clock = make(interval=60, max_age_intervals=3)
    runner.run_once()
    collector.fail = True
    clock.t += 60
    assert not runner.run_once()

    out = fams(cached)
    assert value(out, "whm_collector_up", collector="flaky") == 0
    assert value(out, "whm_collector_errors_total", collector="flaky") == 1
    assert value(out, "whm_collector_runs_total", collector="flaky") == 2
    assert value(out, "whm_test_value") == 1  # still fresh (60s < 180s)

    clock.t += 121  # 181s since last success
    out = fams(cached)
    assert value(out, "whm_collector_stale", collector="flaky") == 1
    assert value(out, "whm_test_value") is None  # dropped, not served stale


def test_recovery_after_failure():
    collector, runner, cached, clock = make()
    collector.fail = True
    runner.run_once()
    out = fams(cached)
    assert value(out, "whm_collector_stale", collector="flaky") == 1
    assert value(out, "whm_collector_last_success_timestamp_seconds", collector="flaky") is None

    collector.fail = False
    runner.run_once()
    out = fams(cached)
    assert value(out, "whm_collector_up", collector="flaky") == 1
    assert value(out, "whm_test_value") == 2


def test_last_error_recorded():
    collector, runner, _, _ = make()
    collector.fail = True
    runner.run_once()
    assert runner.snapshot().last_error == "RuntimeError: WHM is down"


def test_exposition_is_valid_text():
    _, runner, cached, _ = make()
    runner.run_once()
    text = exposition(cached)
    assert "# TYPE whm_collector_errors_total counter" in text
    assert "whm_test_value 1.0" in text
