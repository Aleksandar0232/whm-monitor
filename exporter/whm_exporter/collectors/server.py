from __future__ import annotations

from prometheus_client.core import GaugeMetricFamily

from ..whm_client import WHMClient
from .base import Collector, label


class ServerCollector(Collector):
    """cPanel & WHM version, useful for spotting servers that missed an update."""

    name = "server"

    def __init__(self, client: WHMClient) -> None:
        self.client = client

    def collect(self):
        data = self.client.call("version")
        info = GaugeMetricFamily(
            "whm_server_info",
            "cPanel & WHM build running on the server (value is always 1).",
            labels=["version"],
        )
        info.add_metric([label(data.get("version"), "unknown")], 1)
        return [info]
