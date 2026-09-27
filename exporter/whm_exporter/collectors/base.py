"""Shared pieces for collectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from prometheus_client.metrics_core import Metric


class Collector(ABC):
    """A unit of work that turns one data source into metric families.

    ``collect`` runs in a background thread on its own interval (see
    ``runner.py``); raising any exception marks the run as failed.
    """

    name: str = ""

    @abstractmethod
    def collect(self) -> list[Metric]:
        raise NotImplementedError


def as_int(value: Any, default: int = 0) -> int:
    """WHM returns numbers as ints, strings or null depending on the function."""
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_optional_int(value: Any) -> int | None:
    if value is None or value == "" or str(value).lower() in {"unlimited", "none", "null"}:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def label(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default
