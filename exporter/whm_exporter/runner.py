"""Background refresh of collectors and the cache Prometheus scrapes.

WHM API calls can take seconds (``get_disk_usage``, ``emailtrack_user_stats``
on a busy server), so collectors never run inside a scrape. Each one runs in
its own thread on its own interval and stores its last good result; a scrape
only reads those results.

Cached data is served for ``max_age_intervals`` intervals after the last
success. Past that the collector's series are dropped, so Prometheus sees them
disappear instead of silently alerting on stale values.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.metrics_core import Metric

from .collectors.base import Collector

log = logging.getLogger(__name__)


@dataclass
class Snapshot:
    name: str
    interval: int
    max_age: float
    families: list[Metric] = field(default_factory=list)
    has_run: bool = False
    ok: bool = False
    last_success: float | None = None
    last_duration: float = 0.0
    last_error: str = ""
    runs: int = 0
    errors: int = 0


class CollectorRunner:
    def __init__(
        self,
        collector: Collector,
        interval: int,
        max_age_intervals: int = 3,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.collector = collector
        self.interval = interval
        self.clock = clock
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._state = Snapshot(
            name=collector.name, interval=interval, max_age=interval * max_age_intervals
        )

    @property
    def name(self) -> str:
        return self.collector.name

    def run_once(self) -> bool:
        start = self.monotonic()
        try:
            families = list(self.collector.collect())
        except Exception as exc:  # noqa: BLE001 - any failure marks the run failed
            duration = self.monotonic() - start
            with self._lock:
                self._state.has_run = True
                self._state.ok = False
                self._state.runs += 1
                self._state.errors += 1
                self._state.last_duration = duration
                self._state.last_error = f"{exc.__class__.__name__}: {exc}"
            log.warning("collector %s failed after %.2fs: %s", self.name, duration, exc)
            return False

        duration = self.monotonic() - start
        with self._lock:
            self._state.has_run = True
            self._state.ok = True
            self._state.runs += 1
            self._state.families = families
            self._state.last_success = self.clock()
            self._state.last_duration = duration
            self._state.last_error = ""
        log.debug("collector %s ok in %.2fs", self.name, duration)
        return True

    def snapshot(self) -> Snapshot:
        with self._lock:
            s = self._state
            return Snapshot(
                name=s.name,
                interval=s.interval,
                max_age=s.max_age,
                families=list(s.families),
                has_run=s.has_run,
                ok=s.ok,
                last_success=s.last_success,
                last_duration=s.last_duration,
                last_error=s.last_error,
                runs=s.runs,
                errors=s.errors,
            )

    def loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.run_once()
            stop.wait(self.interval)


class CachedMetrics:
    """prometheus_client collector that serves the runners' cached results."""

    def __init__(self, runners: Iterable[CollectorRunner], clock: Callable[[], float] = time.time):
        self.runners = list(runners)
        self.clock = clock

    def collect(self):
        now = self.clock()
        up = GaugeMetricFamily(
            "whm_collector_up", "1 if the collector's last run succeeded.", labels=["collector"]
        )
        last_success = GaugeMetricFamily(
            "whm_collector_last_success_timestamp_seconds",
            "Unix time of the collector's last successful run.",
            labels=["collector"],
        )
        duration = GaugeMetricFamily(
            "whm_collector_duration_seconds",
            "Duration of the collector's last run.",
            labels=["collector"],
        )
        interval = GaugeMetricFamily(
            "whm_collector_interval_seconds",
            "Configured refresh interval of the collector.",
            labels=["collector"],
        )
        stale = GaugeMetricFamily(
            "whm_collector_stale",
            "1 if the collector's data is older than its max age and is not being served.",
            labels=["collector"],
        )
        runs = CounterMetricFamily(
            "whm_collector_runs", "Collector runs since start.", labels=["collector"]
        )
        errors = CounterMetricFamily(
            "whm_collector_errors", "Failed collector runs since start.", labels=["collector"]
        )

        served: list[Metric] = []
        for runner in self.runners:
            snap = runner.snapshot()
            name = [snap.name]
            interval.add_metric(name, snap.interval)
            runs.add_metric(name, snap.runs)
            errors.add_metric(name, snap.errors)
            if not snap.has_run:
                continue
            up.add_metric(name, 1 if snap.ok else 0)
            duration.add_metric(name, snap.last_duration)
            fresh = snap.last_success is not None and now - snap.last_success <= snap.max_age
            stale.add_metric(name, 0 if fresh else 1)
            if snap.last_success is not None:
                last_success.add_metric(name, snap.last_success)
            if fresh:
                served.extend(snap.families)

        yield from (up, last_success, duration, interval, stale, runs, errors)
        yield from served
