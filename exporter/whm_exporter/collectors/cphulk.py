from __future__ import annotations

from prometheus_client.core import GaugeMetricFamily

from ..whm_client import WHMClient
from .base import Collector, label


class CPHulkCollector(Collector):
    """IP addresses currently blocked by cPHulk brute force protection.

    Needs cPHulk enabled (WHM > Security Center > cPHulk). If you use only
    CSF/LFD, drop this collector from COLLECTORS.
    """

    name = "cphulk"

    def __init__(self, client: WHMClient) -> None:
        self.client = client

    def collect(self):
        blocked = GaugeMetricFamily(
            "whm_cphulk_blocked_ips",
            "IP addresses currently blocked by cPHulk, by block list.",
            labels=["list"],
        )
        for list_name, function, key in (
            ("brute", "get_cphulk_brutes", "brutes"),
            ("excessive", "get_cphulk_excessive_brutes", "excessive_brutes"),
        ):
            data = self.client.call(function)
            entries = data.get(key) or []
            unique_ips = {label(e.get("ip")) for e in entries if isinstance(e, dict)}
            unique_ips.discard("")
            blocked.add_metric([list_name], len(unique_ips))
        return [blocked]
