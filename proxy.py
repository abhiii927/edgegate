from __future__ import annotations

import argparse
import http.client
import http.server
import json
import socketserver
import threading
from pathlib import Path
from typing import Any

from health import BackendServer, start_health_checks
from limiter import RateLimiter

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class PayloadTooLargeError(Exception):
    pass


class BadRequestError(Exception):
    pass


class BackendPool:
    def __init__(self, backends: list[BackendServer]) -> None:
        self.backends = backends
        self._index = 0
        self._lock = threading.Lock()

    def get_candidates(self) -> list[BackendServer]:
        """Returns alive backends in Round-Robin order starting from current index."""
        with self._lock:
            n = len(self.backends)
            if n == 0:
                return []
            start = self._index % n
            self._index = (self._index + 1) % n
            ordered = [self.backends[(start + i) % n] for i in range(n)]
        return [s for s in ordered if s.is_alive]


def strip_hop_by_hop(headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    extra_hop: set[str] = set()
    for key, val in headers:
        if key.lower() == "connection":
            for token in val.split(","):
                cleaned = token.strip().lower()
                if cleaned:
                    extra_hop.add(cleaned)

    blocked = HOP_BY_HOP_HEADERS | extra_hop
    return [
        (k, v)
        for k, v in headers
        if k.lower() not in blocked and not k.lower().startswith("proxy-")
    ]


def parse_backend_spec(spec: str) -> BackendServer:
    if ":" not in spec:
        raise ValueError(f"Invalid backend '{spec}', expected host:port")
    host, port_str = spec.rsplit(":", 1)
    port = int(port_str)
    if not (1 <= port <= 65535):
        raise ValueError(f"Invalid port {port} in '{spec}'")
    return BackendServer(host.strip(), port)


def load_config(config_path: str) -> dict[str, Any]:
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Config file must contain a JSON object")
    return data


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[http.server.BaseHTTPRequestHandler],
        pool: BackendPool,
        limiter: RateLimiter,
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
        max_body_bytes: int = 10 * 1024 * 1024,
        max_header_bytes: int = 32 * 1024,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self.pool = pool
        self.limiter = limiter
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_body_bytes = max_body_bytes
        self.max_header_bytes = max_header_bytes


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ThreadedHTTPServer

    def _send_plain(self, status: int, message: str) -> None:
        body = f"{message}\n".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _check_header_size(self) -> bool:
        total = len(self.requestline) + 2
        for k, v in self.headers.items():
            total += len(k) + len(v) + 4
        return total <= self.server.max_header_bytes

    def _read_chunked_body(self) -> bytes:
        chunks: list[bytes] = []
        total_read = 0
        max_bytes = self.server.max_body_bytes

        while True:
            size_line = self.rfile.readline(65537)
            if not size_line:
                raise BadRequestError("Unexpected EOF in chunked body")
            size_str = size_line.split(b";", 1)[0].strip()
            try:
                chunk_size = int(size_str, 16)
            except ValueError as exc:
                raise BadRequestError("Invalid chunk size") from exc

            if chunk_size < 0:
                raise BadRequestError("Negative chunk size")

            if chunk_size == 0:
                # Consume trailing headers until empty line
                while True:
                    trailer = self.rfile.readline(65537)
                    if not trailer or trailer in (b"\r\n", b"\n"):
                        break
                break

            total_read += chunk_size
            if total_read > max_bytes:
                raise PayloadTooLargeError("Chunked payload exceeds max_body_bytes")

            data = self.rfile.read(chunk_size)
            if len(data) != chunk_size:
                raise BadRequestError("Incomplete chunk payload")
            chunks.append(data)

            crlf = self.rfile.readline(65537)
            if crlf not in (b"\r\n", b"\n"):
                raise BadRequestError("Missing CRLF after chunk payload")

        return b"".join(chunks)

    def _read_request_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in transfer_encoding:
            return self._read_chunked_body()

        content_length_raw = self.headers.get("Content-Length")
        if content_length_raw is None:
            return b""

        try:
            content_length = int(content_length_raw)
        except ValueError as exc:
            raise BadRequestError("Invalid Content-Length header") from exc

        if content_length < 0:
            raise BadRequestError("Negative Content-Length header")
        if content_length > self.server.max_body_bytes:
            raise PayloadTooLargeError("Request body exceeds max_body_bytes")
        if content_length == 0:
            return b""

        body = self.rfile.read(content_length)
        if len(body) != content_length:
            raise BadRequestError("Incomplete request body")
        return body

    def _build_upstream_headers(self, client_ip: str, body_len: int) -> dict[str, str]:
        raw_headers = [(k, v) for k, v in self.headers.items()]
        cleaned = strip_hop_by_hop(raw_headers)

        out: dict[str, str] = {}
        existing_xff = ""
        orig_host = ""

        for k, v in cleaned:
            lk = k.lower()
            if lk == "content-length":
                continue
            if lk == "x-forwarded-for":
                existing_xff = v
                continue
            if lk == "host":
                orig_host = v
            out[k] = v

        out["X-Forwarded-For"] = f"{existing_xff}, {client_ip}" if existing_xff else client_ip
        out["X-Forwarded-Proto"] = "http"
        if orig_host:
            out["X-Forwarded-Host"] = orig_host
        if body_len > 0 or self.command in ("POST", "PUT", "PATCH"):
            out["Content-Length"] = str(body_len)
        out["Connection"] = "close"
        return out

    def _forward_to_backend(
        self,
        backend: BackendServer,
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[int, list[tuple[str, str]], bytes, bool, bool]:
        """Returns (status, headers, response_body, connected_ok, request_ok)."""
        conn = http.client.HTTPConnection(
            backend.host,
            backend.port,
            timeout=self.server.connect_timeout,
        )
        connected = False
        try:
            conn.connect()
            connected = True
            if conn.sock is not None:
                conn.sock.settimeout(self.server.read_timeout)
            conn.request(self.command, self.path, body=body if body else None, headers=headers)
            res = conn.getresponse()
            res_body = b"" if self.command == "HEAD" else res.read(self.server.max_body_bytes + 1)
            if len(res_body) > self.server.max_body_bytes:
                raise PayloadTooLargeError("Upstream response exceeds max_body_bytes")
            res_headers = strip_hop_by_hop(res.getheaders())
            return res.status, res_headers, res_body, True, True
        except PayloadTooLargeError:
            raise
        except (OSError, http.client.HTTPException):
            return 502, [], b"", connected, False
        finally:
            conn.close()

    def _handle_request(self) -> None:
        client_ip = self.client_address[0]

        if not self._check_header_size():
            self._send_plain(431, "431 Request Header Fields Too Large")
            return

        if not self.server.limiter.is_allowed(client_ip):
            self._send_plain(429, "429 Too Many Requests")
            print(f"[limit] blocked {client_ip}")
            return

        try:
            body = self._read_request_body()
        except PayloadTooLargeError as err:
            self._send_plain(413, f"413 Payload Too Large: {err}")
            return
        except BadRequestError as err:
            self._send_plain(400, f"400 Bad Request: {err}")
            return

        candidates = self.server.pool.get_candidates()
        if not candidates:
            self._send_plain(503, "503 Service Unavailable: No healthy backends")
            return

        upstream_headers = self._build_upstream_headers(client_ip, len(body))

        for backend in candidates:
            try:
                status, res_headers, res_body, connected, ok = self._forward_to_backend(
                    backend, body, upstream_headers
                )
            except PayloadTooLargeError:
                self._send_plain(502, "502 Bad Gateway: Upstream response too large")
                return

            if ok:
                self.send_response(status)
                for k, v in res_headers:
                    if k.lower() != "content-length":
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(res_body)))
                self.send_header("X-Served-By", backend.url)
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD" and res_body:
                    self.wfile.write(res_body)
                self.close_connection = True
                print(f"[proxy] {client_ip} {self.command} {self.path} -> {backend.url} ({status})")
                return

            backend.is_alive = False
            print(f"[proxy] upstream failed on {backend.url}")

            # Only retry if connection failed before sending or method is idempotent
            if connected and self.command not in IDEMPOTENT_METHODS:
                break

        self._send_plain(502, "502 Bad Gateway: Upstream request failed")

    def do_GET(self) -> None:
        self._handle_request()

    def do_POST(self) -> None:
        self._handle_request()

    def do_PUT(self) -> None:
        self._handle_request()

    def do_DELETE(self) -> None:
        self._handle_request()

    def do_PATCH(self) -> None:
        self._handle_request()

    def do_HEAD(self) -> None:
        self._handle_request()

    def do_OPTIONS(self) -> None:
        self._handle_request()

    def log_message(self, format: str, *args: Any) -> None:
        pass


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EdgeGate HTTP Reverse Proxy & Rate Limiter")
    parser.add_argument("--config", type=str, help="Path to JSON config file")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="Proxy listen port")
    parser.add_argument(
        "--backends",
        nargs="+",
        default=None,
        help="Upstream backends as host:port (e.g. 127.0.0.1:9001 127.0.0.1:9002)",
    )
    parser.add_argument("--burst", type=int, default=6, help="Max burst tokens per IP")
    parser.add_argument("--rate", type=float, default=2.0, help="Token refill rate per second")
    parser.add_argument("--bucket-ttl", type=float, default=300.0, help="Idle IP bucket TTL (s)")
    parser.add_argument("--max-buckets", type=int, default=10_000, help="Max tracked client IPs")
    parser.add_argument(
        "--connect-timeout", type=float, default=2.0, help="Upstream connect timeout"
    )
    parser.add_argument("--read-timeout", type=float, default=5.0, help="Upstream read timeout")
    parser.add_argument(
        "--max-body-bytes",
        type=int,
        default=10 * 1024 * 1024,
        help="Max request/response body size in bytes",
    )
    parser.add_argument(
        "--max-header-bytes",
        type=int,
        default=32 * 1024,
        help="Max incoming request header size in bytes",
    )
    parser.add_argument(
        "--health-interval",
        type=float,
        default=2.0,
        help="Background TCP health check interval in seconds",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    cfg: dict[str, Any] = load_config(args.config) if args.config else {}

    host = str(cfg.get("host", args.host))
    port = int(cfg.get("port", args.port))
    backend_specs = args.backends or cfg.get("backends") or ["127.0.0.1:9001", "127.0.0.1:9002"]
    backends = [parse_backend_spec(spec) for spec in backend_specs]

    limiter = RateLimiter(
        max_tokens=int(cfg.get("burst", args.burst)),
        refill_rate=float(cfg.get("rate", args.rate)),
        ttl_seconds=float(cfg.get("bucket_ttl", args.bucket_ttl)),
        max_buckets=int(cfg.get("max_buckets", args.max_buckets)),
    )

    stop_event = threading.Event()
    start_health_checks(
        backends,
        interval=float(cfg.get("health_interval", args.health_interval)),
        timeout=float(cfg.get("connect_timeout", args.connect_timeout)),
        stop_event=stop_event,
    )

    server = ThreadedHTTPServer(
        (host, port),
        ProxyHandler,
        pool=BackendPool(backends),
        limiter=limiter,
        connect_timeout=float(cfg.get("connect_timeout", args.connect_timeout)),
        read_timeout=float(cfg.get("read_timeout", args.read_timeout)),
        max_body_bytes=int(cfg.get("max_body_bytes", args.max_body_bytes)),
        max_header_bytes=int(cfg.get("max_header_bytes", args.max_header_bytes)),
    )

    print(f"[edgegate] listening on http://{host}:{port} -> {[b.url for b in backends]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[edgegate] shutting down")
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()