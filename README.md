# edgegate

[![CI](https://github.com/abhiii927/edgegate/actions/workflows/ci.yml/badge.svg)](https://github.com/abhiii927/edgegate/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A lightweight HTTP/1.1 reverse proxy, round-robin load balancer, and token-bucket rate limiter built in Python using only the standard library.

## Features

- **Full HTTP Method & Body Forwarding:** Proxies `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, and `OPTIONS` requests, supporting both `Content-Length` and `Transfer-Encoding: chunked` payloads.
- **RFC-Compliant Header Handling:** Strips hop-by-hop headers (`Connection`, `Keep-Alive`, `Transfer-Encoding`, `Upgrade`, `Proxy-*`) and injects `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host`.
- **Thread-Safe Token Bucket Rate Limiter:** Enforces per-IP burst and refill limits (`429 Too Many Requests`) with automatic TTL + LRU eviction to prevent unbounded memory growth.
- **Active Health Checks & Failover:** Probes upstream TCP sockets in the background, removes unresponsive nodes from rotation, and retries idempotent requests across remaining healthy upstreams.
- **Resource & Timeout Guards:** Enforces configurable connect/read timeouts (`502 Bad Gateway`) alongside header (`431`) and payload (`413`) size caps.

## Installation & Running

Runtime requires **Python 3.11+** with zero third-party dependencies.

    git clone https://github.com/abhiii927/edgegate.git
    cd edgegate

1. Start local upstream servers in separate terminals:

       python3 -m http.server 9001
       python3 -m http.server 9002

2. Start EdgeGate via CLI flags or a JSON config file (`--config config.json`):

       python3 proxy.py --host 127.0.0.1 --port 8080 --backends 127.0.0.1:9001 127.0.0.1:9002 --burst 6 --rate 2.0

3. Run the localhost load-test script (restricted strictly to `127.0.0.1` / `localhost`):

       python3 test_attack.py --url http://127.0.0.1:8080/ --count 12

## Development & Tests

    python3 -m venv .venv
    source .venv/bin/activate
    pip install ruff mypy pytest

    ruff check .
    mypy limiter.py health.py proxy.py test_attack.py tests
    pytest -v

## Current Limitations

- **HTTP/1.1 Short-Lived Upstream Connections:** Upstream connections use `Connection: close` per request rather than persistent connection pooling.
- **No TLS Termination:** Listens over plain HTTP; production deployments should place TLS termination in front of the proxy.
- **Thread-Per-Connection Concurrency:** Uses `socketserver.ThreadingMixIn`, which works well for moderate traffic but does not scale as efficiently as an `asyncio` event loop under tens of thousands of idle connections.
