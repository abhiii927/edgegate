import argparse
import http.server
import socketserver
import urllib.request
import threading
from limiter import RateLimiter
from health import BackendServer, start_health_checks

BACKENDS = [
    BackendServer("127.0.0.1", 9001),
    BackendServer("127.0.0.1", 9002),
]

rr_index = 0
rr_lock = threading.Lock()
rate_limiter = None


def get_next_backend():
    global rr_index
    with rr_lock:
        n = len(BACKENDS)
        for _ in range(n):
            server = BACKENDS[rr_index % n]
            rr_index += 1
            if server.is_alive:
                return server
    return None


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        client_ip = self.client_address[0]

        if not rate_limiter.is_allowed(client_ip):
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b"429 Too Many Requests\n")
            print(f"[limit] blocked {client_ip}")
            return

        target = get_next_backend()
        if not target:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"503 No healthy backends available\n")
            return

        try:
            with urllib.request.urlopen(target.url + self.path, timeout=3) as res:
                body = res.read()
                self.send_response(res.status)
                self.send_header("Content-Type", res.headers.get("Content-Type", "text/html"))
                self.send_header("X-Served-By", target.url)
                self.end_headers()
                self.wfile.write(body)
                print(f"[proxy] {client_ip} -> {target.url}{self.path} ({res.status})")
        except Exception as err:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"502 Bad Gateway: {err}\n".encode())

    def log_message(self, format, *args):
        pass


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EdgeGate HTTP Reverse Proxy")
    parser.add_argument("--port", type=int, default=8080, help="Proxy listen port")
    parser.add_argument("--burst", type=int, default=6, help="Max burst requests per IP")
    parser.add_argument("--rate", type=float, default=2.0, help="Token refill rate per second")
    args = parser.parse_args()

    rate_limiter = RateLimiter(max_tokens=args.burst, refill_rate=args.rate)
    start_health_checks(BACKENDS, interval=2)

    server = ThreadedHTTPServer(("0.0.0.0", args.port), ProxyHandler)
    print(f"[edgegate] listening on :{args.port} (burst={args.burst}, rate={args.rate}/s)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[edgegate] shutting down")
        server.server_close()