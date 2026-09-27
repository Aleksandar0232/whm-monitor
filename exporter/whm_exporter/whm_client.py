"""Minimal client for WHM API 1 (``/json-api/<function>?api.version=1``)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)


class WHMError(RuntimeError):
    """Any failure talking to WHM."""


class WHMAuthError(WHMError):
    """The token was rejected or lacks the privilege for this function."""


class WHMAPIError(WHMError):
    """WHM answered, but reported ``metadata.result == 0``."""


class WHMClient:
    def __init__(
        self,
        base_url: str,
        user: str,
        token: str,
        *,
        verify_tls: bool = True,
        timeout: float = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"whm {user}:{token}",
                "Accept": "application/json",
                "User-Agent": "whm-exporter",
            }
        )
        self.session.verify = verify_tls
        if not verify_tls and self.base_url.startswith("https://"):
            # Expected when talking to https://127.0.0.1:2087, whose certificate
            # is issued for the server hostname. Warn once instead of per request.
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            log.warning("TLS verification for WHM is disabled (WHM_VERIFY_TLS=false)")

        retry = Retry(
            total=2,
            connect=2,
            read=1,
            status=2,
            backoff_factor=0.5,
            status_forcelist=(502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def call(self, function: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Call a WHM API 1 function and return its ``data`` object."""
        query: dict[str, Any] = {"api.version": 1}
        if params:
            query.update(params)
        url = f"{self.base_url}/json-api/{function}"

        try:
            resp = self.session.get(url, params=query, timeout=self.timeout)
        except requests.RequestException as exc:
            raise WHMError(f"{function}: request failed: {exc.__class__.__name__}: {exc}") from exc

        if resp.status_code in (401, 403):
            raise WHMAuthError(
                f"{function}: HTTP {resp.status_code} - token rejected or missing privilege"
            )
        if resp.status_code != 200:
            raise WHMError(f"{function}: unexpected HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise WHMError(f"{function}: response is not JSON") from exc

        metadata = payload.get("metadata") or {}
        if str(metadata.get("result")) != "1":
            reason = str(metadata.get("reason") or "unknown error").strip()
            if "permission" in reason.lower() or "access denied" in reason.lower():
                raise WHMAuthError(f"{function}: {reason}")
            raise WHMAPIError(f"{function}: {reason}")

        data = payload.get("data")
        return data if isinstance(data, dict) else {}
