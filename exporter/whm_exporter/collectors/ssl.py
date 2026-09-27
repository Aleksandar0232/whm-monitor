from __future__ import annotations

from prometheus_client.core import GaugeMetricFamily

from ..whm_client import WHMClient
from .base import Collector, as_int, as_optional_int, label


def _issuer(crt: dict) -> str:
    issuer = crt.get("issuer") or {}
    if isinstance(issuer, dict):
        return label(issuer.get("organizationName") or issuer.get("commonName"), "unknown")
    return "unknown"


class SSLCollector(Collector):
    """Expiry of the certificate installed on every SSL vhost (``fetch_ssl_vhosts``)."""

    name = "ssl"

    def __init__(self, client: WHMClient) -> None:
        self.client = client

    def collect(self):
        data = self.client.call("fetch_ssl_vhosts")
        vhosts = data.get("vhosts") or []

        not_after = GaugeMetricFamily(
            "whm_ssl_cert_not_after_timestamp_seconds",
            "Unix time when the vhost's certificate expires.",
            labels=["servername", "user"],
        )
        self_signed = GaugeMetricFamily(
            "whm_ssl_cert_self_signed",
            "1 if the vhost uses a self-signed certificate.",
            labels=["servername", "user"],
        )
        info = GaugeMetricFamily(
            "whm_ssl_cert_info",
            "Certificate metadata per vhost (value is always 1).",
            labels=["servername", "user", "issuer", "validation_type", "vhost_type"],
        )

        # One series per servername. If WHM ever lists a servername twice
        # (e.g. IPv4 + IPv6 vhost), keep the certificate that expires first.
        best: dict[str, tuple[int, dict, dict]] = {}
        for vhost in vhosts:
            servername = label(vhost.get("servername"))
            crt = vhost.get("crt") or {}
            expires = as_optional_int(crt.get("not_after"))
            if not servername or expires is None:
                continue
            current = best.get(servername)
            if current is None or expires < current[0]:
                best[servername] = (expires, vhost, crt)

        for servername, (expires, vhost, crt) in sorted(best.items()):
            user = label(vhost.get("user"), "nobody")
            not_after.add_metric([servername, user], expires)
            self_signed.add_metric(
                [servername, user], 1 if as_int(crt.get("is_self_signed")) else 0
            )
            info.add_metric(
                [
                    servername,
                    user,
                    _issuer(crt),
                    label(crt.get("validation_type"), "none"),
                    label(vhost.get("type"), "unknown"),
                ],
                1,
            )

        return [not_after, self_signed, info]
