from __future__ import annotations

from prometheus_client.core import GaugeMetricFamily

from ..whm_client import WHMClient
from .base import Collector, as_int, label


class ServicesCollector(Collector):
    """Service state as seen by chkservd (``servicestatus``)."""

    name = "services"

    def __init__(self, client: WHMClient) -> None:
        self.client = client

    def collect(self):
        data = self.client.call("servicestatus")
        services = data.get("service") or []

        families = {
            key: GaugeMetricFamily(f"whm_service_{key}", help_text, labels=["service"])
            for key, help_text in (
                ("installed", "1 if the service is installed."),
                ("enabled", "1 if the service is enabled in WHM."),
                ("monitored", "1 if chkservd monitors the service."),
                ("running", "1 if the service is running (only reported for monitored services)."),
            )
        }

        seen: set[str] = set()
        for svc in services:
            name = label(svc.get("name"))
            if not name or name in seen:
                continue
            seen.add(name)
            for key in ("installed", "enabled", "monitored"):
                families[key].add_metric([name], as_int(svc.get(key)))
            # WHM only includes "running" for monitored services; exporting 0
            # for the others would look like an outage.
            if "running" in svc and svc.get("running") is not None:
                families["running"].add_metric([name], as_int(svc.get("running")))

        return list(families.values())
