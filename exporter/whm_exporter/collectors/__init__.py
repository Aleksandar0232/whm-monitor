from __future__ import annotations

from ..config import Config
from ..whm_client import WHMClient
from .accounts import AccountsCollector
from .base import Collector
from .cphulk import CPHulkCollector
from .disk import DiskCollector
from .email import EmailCollector
from .exim_queue import EximQueueCollector
from .server import ServerCollector
from .services import ServicesCollector
from .ssl import SSLCollector

__all__ = ["Collector", "build_collectors"]


def build_collectors(cfg: Config, client: WHMClient) -> list[Collector]:
    factories = {
        "server": lambda: ServerCollector(client),
        "services": lambda: ServicesCollector(client),
        "accounts": lambda: AccountsCollector(client),
        "disk": lambda: DiskCollector(client),
        "ssl": lambda: SSLCollector(client),
        "cphulk": lambda: CPHulkCollector(client),
        "email": lambda: EmailCollector(client, window=cfg.email_window),
        "exim_queue": lambda: EximQueueCollector(
            cfg.exim_spool_dir, max_files=cfg.exim_max_files, top_n=cfg.exim_top_users
        ),
    }
    return [factories[name]() for name in cfg.collectors]
