"""Exim queue statistics read straight from the spool directory.

``exim -bpc`` is not available inside the container, so the collector reads
the spool (mounted read-only) instead. Every queued message has a ``<id>-H``
file whose first lines are, per the Exim spec ("Format of spool files"):

    1. <message id>-H
    2. <login> <uid> <gid>          local user that submitted the message
    3. <sender address>             "<>" for bounces
    4. <received time> <warnings>
    then option lines starting with "-", e.g. "-frozen <time>", "-auth_id <id>"
    then recipients and the message headers ("NNNX Header: value").

Only the envelope part is read, never the headers or the body.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from prometheus_client.core import GaugeMetricFamily

from .base import Collector

log = logging.getLogger(__name__)

# First line of the header section: 3+ digit length, a flag char, a space.
_HEADER_LINE = re.compile(r"^\d{3,}[ *A-Za-z] ")
# Hard cap on lines read per file, in case a spool file is unusual.
_MAX_LINES = 400


@dataclass
class QueueStats:
    messages: int = 0
    frozen: int = 0
    bounces: int = 0
    oldest_received: int | None = None
    truncated: bool = False
    unreadable: int = 0
    by_local_user: Counter = field(default_factory=Counter)
    by_auth_domain: Counter = field(default_factory=Counter)


def iter_header_files(spool_dir: str, max_depth: int = 2) -> Iterator[str]:
    """Yield ``*-H`` files in the spool, including split_spool_directory subdirs."""
    stack = [(spool_dir, 0)]
    while stack:
        path, depth = stack.pop()
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth + 1 < max_depth:
                                stack.append((entry.path, depth + 1))
                        elif entry.name.endswith("-H"):
                            yield entry.path
                    except OSError:
                        continue
        except FileNotFoundError:
            if depth == 0:
                raise
        except PermissionError:
            if depth == 0:
                raise
            log.debug("cannot read spool subdirectory %s", path)


def parse_header_file(path: str) -> dict | None:
    """Return the envelope fields of one ``-H`` file, or None if it vanished."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = []
            for _ in range(4):
                lines.append(fh.readline().rstrip("\n"))
            frozen = False
            auth_id = ""
            for _ in range(_MAX_LINES):
                line = fh.readline()
                if not line or _HEADER_LINE.match(line):
                    break
                if line.startswith("-frozen "):
                    frozen = True
                elif line.startswith("-auth_id "):
                    auth_id = line[len("-auth_id ") :].strip()
    except FileNotFoundError:
        return None  # delivered while we were scanning

    login = lines[1].split()[0] if lines[1].split() else ""
    try:
        received = int(lines[3].split()[0])
    except (IndexError, ValueError):
        received = None
    return {
        "login": login,
        "sender": lines[2].strip(),
        "received": received,
        "frozen": frozen,
        "auth_id": auth_id,
    }


def scan_queue(spool_dir: str, max_files: int) -> QueueStats:
    stats = QueueStats()
    for path in iter_header_files(spool_dir):
        if stats.messages >= max_files:
            stats.truncated = True
            break
        try:
            parsed = parse_header_file(path)
        except OSError:
            stats.unreadable += 1
            continue
        if parsed is None:
            continue
        stats.messages += 1
        if parsed["frozen"]:
            stats.frozen += 1
        if parsed["sender"] == "<>":
            stats.bounces += 1
        received = parsed["received"]
        if received is not None and (
            stats.oldest_received is None or received < stats.oldest_received
        ):
            stats.oldest_received = received
        if parsed["login"]:
            stats.by_local_user[parsed["login"]] += 1
        auth_id = parsed["auth_id"]
        if "@" in auth_id:
            stats.by_auth_domain[auth_id.rsplit("@", 1)[1].lower()] += 1
    return stats


class EximQueueCollector(Collector):
    name = "exim_queue"

    def __init__(
        self,
        spool_dir: str,
        max_files: int = 50000,
        top_n: int = 10,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.spool_dir = spool_dir
        self.max_files = max_files
        self.top_n = top_n
        self.clock = clock

    def collect(self):
        if not os.path.isdir(self.spool_dir):
            raise RuntimeError(
                f"Exim spool {self.spool_dir!r} not found - is /var/spool/exim/input mounted?"
            )
        hint = "check EXIM_SPOOL_GID in .env (run: stat -c %g /var/spool/exim/input)"
        try:
            stats = scan_queue(self.spool_dir, self.max_files)
        except PermissionError as exc:
            raise PermissionError(f"cannot list {self.spool_dir}: {exc} - {hint}") from exc
        if stats.unreadable and stats.messages == 0:
            raise PermissionError(f"cannot read any spool file in {self.spool_dir} - {hint}")

        def gauge(name: str, help_text: str, value: float) -> GaugeMetricFamily:
            fam = GaugeMetricFamily(name, help_text)
            fam.add_metric([], value)
            return fam

        now = self.clock()
        oldest_age = (
            max(0.0, now - stats.oldest_received) if stats.oldest_received is not None else 0.0
        )

        families = [
            gauge("whm_exim_queue_messages", "Messages in the Exim queue.", stats.messages),
            gauge("whm_exim_queue_frozen_messages", "Frozen messages in the queue.", stats.frozen),
            gauge(
                "whm_exim_queue_bounce_messages",
                "Queued bounces (empty envelope sender).",
                stats.bounces,
            ),
            gauge(
                "whm_exim_queue_oldest_message_age_seconds",
                "Age of the oldest queued message (0 when the queue is empty).",
                oldest_age,
            ),
            gauge(
                "whm_exim_queue_scan_truncated",
                "1 if the scan stopped at EXIM_MAX_FILES; counts are then lower bounds.",
                1 if stats.truncated else 0,
            ),
            gauge(
                "whm_exim_queue_unreadable_files",
                "Spool header files that could not be read (permissions).",
                stats.unreadable,
            ),
        ]

        by_user = GaugeMetricFamily(
            "whm_exim_queue_messages_by_local_user",
            "Queued messages by submitting local user, top N. 'mailnull' means received over SMTP.",
            labels=["user"],
        )
        for user, count in stats.by_local_user.most_common(self.top_n):
            by_user.add_metric([user], count)

        by_domain = GaugeMetricFamily(
            "whm_exim_queue_messages_by_auth_domain",
            "Queued messages sent with SMTP authentication, by the domain of the "
            "authenticated account, top N.",
            labels=["domain"],
        )
        for domain, count in stats.by_auth_domain.most_common(self.top_n):
            by_domain.add_metric([domain], count)

        families.extend([by_user, by_domain])
        return families
