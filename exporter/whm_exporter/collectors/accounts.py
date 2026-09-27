from __future__ import annotations

from prometheus_client.core import GaugeMetricFamily

from ..whm_client import WHMClient
from .base import Collector, as_int, label


class AccountsCollector(Collector):
    """cPanel accounts: totals, suspension state and account metadata."""

    name = "accounts"

    def __init__(self, client: WHMClient) -> None:
        self.client = client

    def collect(self):
        data = self.client.call("listaccts", {"want": "user,domain,suspended,plan,owner"})
        accounts = data.get("acct") or []

        info = GaugeMetricFamily(
            "whm_account_info",
            "cPanel account metadata (value is always 1).",
            labels=["user", "domain", "plan", "owner"],
        )
        suspended = GaugeMetricFamily(
            "whm_account_suspended", "1 if the cPanel account is suspended.", labels=["user"]
        )
        totals = GaugeMetricFamily(
            "whm_accounts", "Number of cPanel accounts by state.", labels=["state"]
        )

        counts = {"active": 0, "suspended": 0}
        seen: set[str] = set()
        for acct in accounts:
            user = label(acct.get("user"))
            if not user or user in seen:
                continue
            seen.add(user)
            is_suspended = 1 if as_int(acct.get("suspended")) else 0
            counts["suspended" if is_suspended else "active"] += 1
            info.add_metric(
                [
                    user,
                    label(acct.get("domain")),
                    label(acct.get("plan"), "default"),
                    label(acct.get("owner"), "root"),
                ],
                1,
            )
            suspended.add_metric([user], is_suspended)

        for state, value in counts.items():
            totals.add_metric([state], value)

        return [totals, info, suspended]
