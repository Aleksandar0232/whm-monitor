from __future__ import annotations

from prometheus_client.core import GaugeMetricFamily

from ..whm_client import WHMClient
from .base import Collector, as_int, as_optional_int, label

KIB = 1024


class DiskCollector(Collector):
    """Per-account disk and inode usage from quotas (``get_disk_usage``)."""

    name = "disk"

    def __init__(self, client: WHMClient) -> None:
        self.client = client

    def collect(self):
        # cache_mode=on reads WHM's quota cache: much cheaper on big servers and
        # fresh enough for a 5 minute interval.
        data = self.client.call("get_disk_usage", {"cache_mode": "on"})
        accounts = data.get("accounts") or []

        used = GaugeMetricFamily(
            "whm_account_disk_used_bytes", "Disk space used by the account.", labels=["user"]
        )
        limit = GaugeMetricFamily(
            "whm_account_disk_limit_bytes",
            "Disk quota of the account (absent when unlimited).",
            labels=["user"],
        )
        inodes = GaugeMetricFamily(
            "whm_account_inodes_used", "Inodes (files and directories) used.", labels=["user"]
        )
        inodes_limit = GaugeMetricFamily(
            "whm_account_inodes_limit",
            "Inode quota of the account (absent when unlimited).",
            labels=["user"],
        )

        seen: set[str] = set()
        for acct in accounts:
            user = label(acct.get("user"))
            if not user or user in seen:
                continue
            seen.add(user)
            used.add_metric([user], as_int(acct.get("blocks_used")) * KIB)
            inodes.add_metric([user], as_int(acct.get("inodes_used")))
            block_limit = as_optional_int(acct.get("blocks_limit"))
            if block_limit:  # null/0 both mean unlimited
                limit.add_metric([user], block_limit * KIB)
            inode_limit = as_optional_int(acct.get("inodes_limit"))
            if inode_limit:
                inodes_limit.add_metric([user], inode_limit)

        return [used, limit, inodes, inodes_limit]
