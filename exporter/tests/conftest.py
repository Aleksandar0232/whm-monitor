from __future__ import annotations

import sys
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry

sys.path.insert(0, str(Path(__file__).parent))

from fake_whm import FakeWHM  # noqa: E402

from whm_exporter.whm_client import WHMClient  # noqa: E402

TOKEN = "testtoken"


@pytest.fixture
def fake_whm():
    server = FakeWHM(token=TOKEN).start()
    yield server
    server.stop()


@pytest.fixture
def client(fake_whm):
    return WHMClient(fake_whm.url, "root", TOKEN, timeout=5)


def samples(families) -> dict[tuple[str, tuple], float]:
    """Flatten metric families into {(sample_name, sorted label items): value}."""
    out = {}
    for fam in families:
        for s in fam.samples:
            out[(s.name, tuple(sorted(s.labels.items())))] = s.value
    return out


def value(families, name: str, **labels) -> float | None:
    return samples(families).get((name, tuple(sorted(labels.items()))))


def exposition(*collectors) -> str:
    from prometheus_client import generate_latest

    registry = CollectorRegistry()
    for c in collectors:
        registry.register(c)
    return generate_latest(registry).decode()
