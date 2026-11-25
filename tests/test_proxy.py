from __future__ import annotations

import http.client
import http.server
import socketserver
import threading
from collections.abc import Generator

import pytest

from health import BackendServer
from limiter import RateLimiter
from proxy import BackendPool, ProxyHandler, ThreadedHTTPServer


class EchoUpstreamHandler(http.server.BaseHTTPRequestHandler):
    def _respond(self) -> None:
        if self.path == "/not-found":
            body_404 = b"missing"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body_404)))
            self.end_headers()
            self.wfile.write(body_404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b""
        payload = (
            f"method={self.command}|path={self.path}|"
            f"xff={self.headers.get('X-Forwarded-For', '')}|"
            f"proto={self.headers.get('X-Forwarded-Proto', '')}|"
            f"xfh={self.headers.get('X-Forwarded-Host', '')}|"
            f"conn={self.headers.get('Connection', '')}|"
            f"hop={self.headers.get('X-Custom-Hop', '')}|"
            f"proxy_hdr={self.headers.get('Proxy-Agent', '')}|"
            f"body={body.decode('utf-8')}"
        ).encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Keep-Alive", "timeout=5")  # Hop-by-hop header to strip
        self.send_header("Proxy-Agent", "upstream/1.0")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        self._respond()

    def do_PUT(self) -> None:
        self._respond()

    def do_DELETE(self) -> None:
        self._respond()

    def do_PATCH(self) -> None:
        self._respond()

    def do_HEAD(self) -> None:
        self._respond()

    def do_OPTIONS(self) -> None:
        self._respond()

    def log_message(self, format: str, *args: object) -> None:
        pass


class _TestUpstream(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


@pytest.fixture()
def proxy_env() -> Generator[tuple[int, int, int], None, None]:
    up1 = _TestUpstream(("127.0.0.1", 0), EchoUpstreamHandler)
    up2 = _TestUpstream(("127.0.0.1", 0), EchoUpstreamHandler)
    p1 = up1.server_address[1]
    p2 = up2.server_address[1]

    threading.Thread(target=up1.serve_forever, daemon=True).start()
    threading.Thread(target=up2.serve_forever, daemon=True).start()

    backends = [BackendServer("127.0.0.1", p1), BackendServer("127.0.0.1", p2)]
    proxy = ThreadedHTTPServer(
        ("127.0.0.1", 0),
        ProxyHandler,
        pool=BackendPool(backends),
        limiter=RateLimiter(max_tokens=50, refill_rate=50.0),
        max_body_bytes=1024,
        max_header_bytes=1024,
    )
    proxy_port = proxy.server_address[1]
    threading.Thread(target=proxy.serve_forever, daemon=True).start()

    try:
        yield proxy_port, p1, p2
    finally:
        proxy.shutdown()
        proxy.server_close()
        up1.shutdown()
        up1.server_close()
        up2.shutdown()
        up2.server_close()


def test_round_robin_routing_and_forwarded_headers(proxy_env: tuple[int, int, int]) -> None:
    proxy_port, p1, p2 = proxy_env
    served_ports: list[str] = []

    for _ in range(2):
        conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
        conn.request(
            "GET",
            "/healthz",
            headers={
                "Host": "edge.local:8080",
                "Connection": "X-Custom-Hop",
                "X-Custom-Hop": "secret",
                "Proxy-Agent": "client/1.0",
            },
        )
        res = conn.getresponse()
        data = res.read().decode("utf-8")
        served_by = res.getheader("X-Served-By", "")
        served_ports.append(served_by.rsplit(":", 1)[1])
        assert res.status == 200
        assert res.getheader("Keep-Alive") is None
        assert res.getheader("Proxy-Agent") is None
        assert "xff=127.0.0.1" in data
        assert "proto=http" in data
        assert "xfh=edge.local:8080" in data
        assert "hop=|" in data
        assert "proxy_hdr=|" in data
        conn.close()

    assert served_ports == [str(p1), str(p2)]


def test_post_put_delete_and_chunked_body_forwarding(proxy_env: tuple[int, int, int]) -> None:
    proxy_port, _, _ = proxy_env

    # Standard POST with Content-Length
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
    conn.request(
        "POST", "/items", body=b'{"name":"edge"}', headers={"Content-Type": "application/json"}
    )
    res = conn.getresponse()
    text = res.read().decode("utf-8")
    assert res.status == 200
    assert "method=POST|path=/items|" in text
    assert 'body={"name":"edge"}' in text
    conn.close()

    # Chunked PUT request
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
    conn.putrequest("PUT", "/chunked")
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    conn.send(b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n")
    res = conn.getresponse()
    text = res.read().decode("utf-8")
    assert res.status == 200
    assert "method=PUT|path=/chunked|" in text
    assert "body=hello world" in text
    conn.close()

    # DELETE request
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
    conn.request("DELETE", "/items/1")
    res = conn.getresponse()
    text = res.read().decode("utf-8")
    assert res.status == 200
    assert "method=DELETE|path=/items/1|" in text
    conn.close()


def test_upstream_4xx_passthrough_and_size_limits(proxy_env: tuple[int, int, int]) -> None:
    proxy_port, _, _ = proxy_env

    # 4xx from upstream is passed through transparently
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
    conn.request("GET", "/not-found")
    res = conn.getresponse()
    body = res.read()
    assert res.status == 404
    assert body == b"missing"
    conn.close()

    # Oversized body -> 413
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
    conn.request("POST", "/items", body=b"x" * 2048)
    res = conn.getresponse()
    res.read()
    assert res.status == 413
    conn.close()

    # Oversized headers -> 431
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
    conn.request("GET", "/", headers={"X-Big": "a" * 2048})
    res = conn.getresponse()
    res.read()
    assert res.status == 431
    conn.close()


def test_dead_backend_failover_and_502_when_all_fail() -> None:
    up = _TestUpstream(("127.0.0.1", 0), EchoUpstreamHandler)
    up_port = up.server_address[1]
    threading.Thread(target=up.serve_forever, daemon=True).start()

    dead_backend = BackendServer("127.0.0.1", 59999)
    live_backend = BackendServer("127.0.0.1", up_port)

    # First test failover from dead_backend to live_backend
    proxy = ThreadedHTTPServer(
        ("127.0.0.1", 0),
        ProxyHandler,
        pool=BackendPool([dead_backend, live_backend]),
        limiter=RateLimiter(max_tokens=10, refill_rate=10.0),
        connect_timeout=0.2,
    )
    port = proxy.server_address[1]
    threading.Thread(target=proxy.serve_forever, daemon=True).start()

    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/failover")
        res = conn.getresponse()
        res.read()
        assert res.status == 200
        assert dead_backend.is_alive is False
        assert res.getheader("X-Served-By") == f"http://127.0.0.1:{up_port}"
        conn.close()
    finally:
        proxy.shutdown()
        proxy.server_close()
        up.shutdown()
        up.server_close()

    # Next test 502 when all backends fail
    all_dead = ThreadedHTTPServer(
        ("127.0.0.1", 0),
        ProxyHandler,
        pool=BackendPool([BackendServer("127.0.0.1", 59999)]),
        limiter=RateLimiter(max_tokens=10, refill_rate=10.0),
        connect_timeout=0.2,
    )
    dead_port = all_dead.server_address[1]
    threading.Thread(target=all_dead.serve_forever, daemon=True).start()

    try:
        conn = http.client.HTTPConnection("127.0.0.1", dead_port, timeout=2)
        conn.request("GET", "/")
        res = conn.getresponse()
        res.read()
        assert res.status == 502
        conn.close()
    finally:
        all_dead.shutdown()
        all_dead.server_close()
