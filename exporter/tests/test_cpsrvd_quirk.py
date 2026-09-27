"""cpsrvd can leak a plain-text line into the HTTP header block.

Seen on cPanel 11.138 with ``servicestatus``:

    Apache PHP-FPM 82(php-fpm: master process (...)) is running as root with
    PID "4120797" (process table check method)

Python's header parser stops at that line and drops Content-Length and
Content-Encoding, so a naive client waits for a keep-alive connection to close
and times out. These tests imitate that server exactly.
"""

import gzip
import json
import socketserver
import threading
import time

import pytest

from whm_exporter.whm_client import WHMClient, WHMError

JUNK = (
    "Apache PHP-FPM 82(php-fpm: master process (/opt/cpanel/ea-php82/root/etc/php-fpm.conf)) "
    "is running as root with PID “4120797” (process table check method)"
).encode()

PAYLOAD = {
    "metadata": {"command": "servicestatus", "result": 1, "reason": "OK", "version": 1},
    "data": {
        "service": [{"name": "httpd", "enabled": 1, "installed": 1, "monitored": 1, "running": 1}]
    },
}


class QuirkyServer:
    def __init__(self, *, honor_close: bool, split_body: bool = False, junk: bool = True):
        self.requests: list[str] = []
        self.release = threading.Event()
        outer = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                request = data.decode("latin-1").lower()
                outer.requests.append(request)

                body = json.dumps(PAYLOAD).encode()
                encoding = b""
                if "gzip" in request.split("accept-encoding:", 1)[-1].split("\r\n", 1)[0]:
                    body = gzip.compress(body)
                    encoding = b"Content-Encoding: gzip\r\n"
                head = (
                    b"HTTP/1.1 200 OK\r\nConnection: Keep-Alive\r\n"
                    b'Content-Type: application/json; charset="utf-8"\r\n'
                    + (JUNK + b"\r\n" if junk else b"")
                    + b"Cache-Control: no-cache\r\n"
                    + encoding
                    + b"Content-Length: %d\r\n\r\n" % len(body)
                )
                if split_body:
                    # First part ends with "}" but is not complete JSON yet.
                    cut = body.index(b"}") + 1
                    self.request.sendall(head + body[:cut])
                    time.sleep(0.2)
                    self.request.sendall(body[cut:])
                else:
                    self.request.sendall(head + body)

                if honor_close and "connection: close" in request:
                    return  # handler returns -> socket closed
                outer.release.wait(10)  # keep-alive: hold the connection open

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def quirky(request):
    servers = []

    def make(**kwargs):
        s = QuirkyServer(**kwargs)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.stop()


def call(server, timeout=5):
    client = WHMClient(server.url, "root", "t", timeout=timeout)
    start = time.monotonic()
    data = client.call("servicestatus")
    return data, time.monotonic() - start


def test_sends_connection_close_and_identity(quirky):
    server = quirky(honor_close=True, junk=False)
    call(server)
    req = server.requests[-1]
    assert "connection: close" in req
    assert "accept-encoding: identity" in req


def test_leaked_line_server_closes(quirky):
    data, elapsed = call(quirky(honor_close=True))
    assert data["service"][0]["name"] == "httpd"
    assert elapsed < 2


def test_leaked_line_server_keeps_connection_open(quirky):
    # Even if cpsrvd ignored "Connection: close", we must not wait for EOF.
    data, elapsed = call(quirky(honor_close=False))
    assert data["service"][0]["running"] == 1
    assert elapsed < 2


def test_leaked_line_body_arrives_in_pieces(quirky):
    data, elapsed = call(quirky(honor_close=False, split_body=True))
    assert data["service"][0]["name"] == "httpd"
    assert elapsed < 2


def test_truncated_body_then_timeout_is_an_error(quirky):
    # Server that sends half a JSON document and then nothing: fail, don't hang.
    server = quirky(honor_close=False)
    server.server.RequestHandlerClass.handle = _half_handler(server)
    with pytest.raises(WHMError, match="request failed|not JSON"):
        call(server, timeout=1)


def _half_handler(server):
    def handle(self):
        self.request.recv(4096)
        self.request.sendall(
            b"HTTP/1.1 200 OK\r\nConnection: Keep-Alive\r\n" + JUNK + b"\r\n"
            b"Content-Length: 999\r\n\r\n" + b'{"metadata": {"result": 1'
        )
        server.release.wait(10)

    return handle


def test_socket_level_sanity():
    # Guard: the fixture really produces the defect Python trips over.
    import http.client
    import io

    raw = b"Connection: Keep-Alive\r\n" + JUNK + b"\r\nContent-Length: 5\r\n\r\n"
    msg = http.client.parse_headers(io.BytesIO(raw))
    assert "Content-Length" not in msg
    assert msg.defects
