"""Entry point: ``python -m whm_exporter [--check | --once | --healthcheck]``."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import urllib.request

from prometheus_client import CollectorRegistry, generate_latest, start_http_server
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

from . import __version__
from .collectors import build_collectors
from .config import Config, ConfigError
from .runner import CachedMetrics, CollectorRunner
from .whm_client import WHMClient

log = logging.getLogger("whm_exporter")


class BuildInfo:
    def collect(self):
        fam = GaugeMetricFamily(
            "whm_exporter_build_info", "Exporter version (value is always 1).", labels=["version"]
        )
        fam.add_metric([__version__], 1)
        yield fam


def healthcheck() -> int:
    addr = os.environ.get("EXPORTER_LISTEN_ADDR", "127.0.0.1") or "127.0.0.1"
    if addr in ("0.0.0.0", "::"):
        addr = "127.0.0.1"
    port = os.environ.get("EXPORTER_PORT", "9877") or "9877"
    host = f"[{addr}]" if ":" in addr else addr
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/metrics", timeout=5) as resp:
            return 0 if resp.status == 200 else 1
    except OSError as exc:
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1


def build_runners(cfg: Config) -> list[CollectorRunner]:
    client = WHMClient(
        cfg.whm_url,
        cfg.whm_user,
        cfg.whm_token,
        verify_tls=cfg.verify_tls,
        timeout=cfg.request_timeout,
    )
    return [
        CollectorRunner(c, cfg.intervals[c.name], cfg.max_age_intervals)
        for c in build_collectors(cfg, client)
    ]


def build_registry(runners: list[CollectorRunner]) -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(BuildInfo())
    registry.register(ProcessCollector(registry=None))
    registry.register(PlatformCollector(registry=None))
    registry.register(CachedMetrics(runners))
    return registry


def run_check(runners: list[CollectorRunner]) -> int:
    """Run every collector once and print a table. Handy right after creating the token."""
    failed = 0
    print(f"{'COLLECTOR':<12} {'STATUS':<6} {'TIME':>7}  DETAIL")
    for runner in runners:
        ok = runner.run_once()
        snap = runner.snapshot()
        series = sum(len(f.samples) for f in snap.families) if ok else 0
        detail = f"{series} series" if ok else snap.last_error
        print(
            f"{runner.name:<12} {'OK' if ok else 'FAIL':<6} {snap.last_duration:>6.2f}s  {detail}"
        )
        failed += 0 if ok else 1
    if failed:
        print(
            f"\n{failed} collector(s) failed. Permission errors usually mean the API token "
            "is missing an ACL; drop collectors you do not need with COLLECTORS=...",
            file=sys.stderr,
        )
    return 1 if failed else 0


def serve(cfg: Config, runners: list[CollectorRunner]) -> int:
    registry = build_registry(runners)
    stop = threading.Event()

    def _shutdown(signum, _frame):
        log.info("received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    threads = []
    for i, runner in enumerate(runners):

        def _loop(r=runner, delay=i * 0.5):
            # Stagger the first runs so all WHM calls do not start at once.
            if not stop.wait(delay):
                r.loop(stop)

        t = threading.Thread(target=_loop, name=f"collector-{runner.name}", daemon=True)
        t.start()
        threads.append(t)

    server, _ = start_http_server(cfg.listen_port, addr=cfg.listen_addr, registry=registry)
    log.info(
        "whm-exporter %s listening on %s:%d, collectors: %s",
        __version__,
        cfg.listen_addr,
        cfg.listen_port,
        ", ".join(f"{r.name}({r.interval}s)" for r in runners),
    )

    stop.wait()
    server.shutdown()
    for t in threads:
        t.join(timeout=5)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whm-exporter", description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="run each collector once and report")
    mode.add_argument("--once", action="store_true", help="collect once and print metrics")
    mode.add_argument("--healthcheck", action="store_true", help="exit 0 if /metrics answers")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    if args.healthcheck:
        return healthcheck()

    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    runners = build_runners(cfg)

    if args.check:
        return run_check(runners)
    if args.once:
        for runner in runners:
            runner.run_once()
        sys.stdout.write(generate_latest(build_registry(runners)).decode())
        return 0
    return serve(cfg, runners)


if __name__ == "__main__":
    sys.exit(main())
