from __future__ import annotations

import time
from collections.abc import Callable

from prometheus_client.core import GaugeMetricFamily

from ..whm_client import WHMClient
from .base import Collector, as_int, label

# emailtrack field -> "result" label value
RESULT_FIELDS = {
    "SENDCOUNT": "sent",
    "SUCCESSCOUNT": "delivered",
    "FAILCOUNT": "failed",
    "DEFERCOUNT": "deferred",
}


class EmailCollector(Collector):
    """Outgoing mail per cPanel user over a sliding window (``emailtrack_user_stats``).

    The values are totals for the last ``window`` seconds, not counters: use
    them directly in alerts ("more than 500 messages in the last hour").
    """

    name = "email"

    def __init__(
        self, client: WHMClient, window: int, clock: Callable[[], float] = time.time
    ) -> None:
        self.client = client
        self.window = window
        self.clock = clock

    def collect(self):
        end = int(self.clock())
        data = self.client.call(
            "emailtrack_user_stats", {"starttime": end - self.window, "endtime": end}
        )
        records = data.get("records") or []

        # Aggregate per user so a user never appears twice with the same labels.
        per_user: dict[str, dict] = {}
        for rec in records:
            user = label(rec.get("USER"))
            if not user:
                continue
            agg = per_user.setdefault(
                user,
                {
                    "domain": label(rec.get("PRIMARY_DOMAIN") or rec.get("DOMAIN")),
                    "max_emails": 0,
                    "max_defer_fail": 0,
                    **{result: 0 for result in RESULT_FIELDS.values()},
                },
            )
            for field_name, result in RESULT_FIELDS.items():
                agg[result] += as_int(rec.get(field_name))
            agg["max_emails"] = max(agg["max_emails"], as_int(rec.get("REACHED_MAXEMAILS")))
            agg["max_defer_fail"] = max(
                agg["max_defer_fail"], as_int(rec.get("REACHED_MAXDEFERFAIL"))
            )

        messages = GaugeMetricFamily(
            "whm_email_messages",
            "Outgoing messages per cPanel user in the last whm_email_window_seconds, by result.",
            labels=["user", "domain", "result"],
        )
        reached_max = GaugeMetricFamily(
            "whm_email_reached_max_per_hour",
            "1 if the user hit the 'Max hourly emails per domain' limit in the window.",
            labels=["user"],
        )
        reached_defer = GaugeMetricFamily(
            "whm_email_reached_max_defer_fail",
            "1 if the user hit the 'Max percentage of failed or deferred messages' limit.",
            labels=["user"],
        )
        window = GaugeMetricFamily(
            "whm_email_window_seconds", "Length of the window the email metrics cover."
        )
        window.add_metric([], self.window)

        for user, agg in sorted(per_user.items()):
            for result in RESULT_FIELDS.values():
                messages.add_metric([user, agg["domain"], result], agg[result])
            reached_max.add_metric([user], 1 if agg["max_emails"] else 0)
            reached_defer.add_metric([user], 1 if agg["max_defer_fail"] else 0)

        return [messages, reached_max, reached_defer, window]
