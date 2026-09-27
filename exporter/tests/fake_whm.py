"""A tiny fake WHM API 1 server for tests and local development.

Serves ``tests/fixtures/<function>.json`` at ``/json-api/<function>`` and
checks the ``Authorization: whm <user>:<token>`` header like WHM does.

Run it standalone to try the exporter without a cPanel server:

    python tests/fake_whm.py --port 8087 --token devtoken
    WHM_URL=http://127.0.0.1:8087 WHM_API_TOKEN=devtoken python -m whm_exporter --check
"""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).parent / "fixtures"


class FakeWHM:
    def __init__(self, user: str = "root", token: str = "testtoken", port: int = 0):
        self.expected_auth = f"whm {user}:{token}"
        self.requests: list[tuple[str, dict]] = []
        # function -> (http_status, payload) to simulate failures
        self.overrides: dict[str, tuple[int, dict | str]] = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def _send(self, status: int, body: dict | str) -> None:
                raw = body if isinstance(body, str) else json.dumps(body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw.encode())

            def do_GET(self):
                url = urlparse(self.path)
                params = {k: v[0] for k, v in parse_qs(url.query).items()}
                function = url.path.rsplit("/", 1)[-1]
                fake.requests.append((function, params))

                if self.headers.get("Authorization") != fake.expected_auth:
                    self._send(403, "<html>Access denied</html>")
                    return
                if not url.path.startswith("/json-api/") or params.get("api.version") != "1":
                    self._send(404, {"error": "unknown endpoint"})
                    return
                if function in fake.overrides:
                    self._send(*fake.overrides[function])
                    return
                fixture = FIXTURES / f"{function}.json"
                if not fixture.exists():
                    self._send(
                        200,
                        {
                            "metadata": {
                                "command": function,
                                "result": 0,
                                "reason": f"Unknown app (“{function}”) requested",
                            }
                        },
                    )
                    return
                self._send(200, json.loads(fixture.read_text()))

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "FakeWHM":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8087)
    parser.add_argument("--token", default="devtoken")
    args = parser.parse_args()
    fake = FakeWHM(token=args.token, port=args.port)
    print(f"fake WHM listening on {fake.url} (token: {args.token})")
    with contextlib.suppress(KeyboardInterrupt):
        fake.server.serve_forever()
