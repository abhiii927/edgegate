# edgegate

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A lightweight, zero-dependency HTTP/1.1 reverse proxy, round-robin load balancer, and token-bucket rate limiter written using only the Python standard library.

## Features

- **Full HTTP Method & Body Forwarding:** Supports `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, and `OPTIONS`, forwarding both `Content-Length` and `Transfer-Encoding: chunked` request bodies.
- **Transparent Upstream Responses:** Uses `http.client.HTTPConnection` so upstream `4xx` responses pass through as-is instead of being masked as `502`.
- **RFC-Compliant Header Sanitization:** Strips hop-by-hop headers (`Connection`, `Keep-Alive`, `Transfer-Encoding`, `Upgrade`, `TE`, `Trailer`, `Proxy-*`, and any headers named in `Connection`) and sets/appends `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host`.
- **Thread-Safe Load Balancing & Failover:** Guards the round-robin index, per-IP rate-limiter buckets, and backend health flags with `threading.Lock`. Automatically marks failed upstreams unhealthy and retries remaining healthy nodes for idempotent methods or pre-send connection failures.
- **Bounded-Memory Rate Limiter:** Per-IP token bucket with `OrderedDict` LRU eviction and idle TTL expiration (`429 Too Many Requests` when exhausted).
- **Timeouts & Size Limits:** Configurable connect/read timeouts (`502 Bad Gateway` on failure), max request header size (`431 Request Header Fields Too Large`), and max body size (`413 Payload Too Large`).
- **Background Health Checks:** Periodic TCP probes remove unreachable backends from rotation and restore them when they recover.

## Architecture

```text
client (:8080)
  │
  ├─► Header & Body Size Checks (431 / 413)
  ├─► RateLimiter (per-IP token bucket, LRU + TTL) ──► 429 if empty
  ├─► HealthMonitor (TCP probe every 2s)
  │
  ├─► 127.0.0.1:9001
  └─► 127.0.0.1:9002
```

## Running

Requires **Python 3.11+** with **zero third-party runtime dependencies**.

Start two local test upstreams (for example, in separate terminals):

```bash
python3 -m http.server 9001
python3 -m http.server 9002
```

Run `edgegate` with CLI flags:

```bash
python3 proxy.py \
  --host 127.0.0.1 \
  --port 8080 \
  --backends 127.0.0.1:9001 127.0.0.1:9002 \
  --burst 6 \
  --rate 2.0 \
  --connect-timeout 2.0 \
  --read-timeout 5.0
```

Or load settings from a JSON config file:

```json
{
  "host": "127.0.0.1",
  "port": 8080,
  "backends": ["127.0.0.1:9001", "127.0.0.1:9002"],
  "burst": 10,
  "rate": 5.0,
  "bucket_ttl": 300.0,
  "max_buckets": 10000,
  "connect_timeout": 2.0,
  "read_timeout": 5.0,
  "max_body_bytes": 10485760,
  "max_header_bytes": 32768,
  "health_interval": 2.0
}
```

```bash
python3 proxy.py --config config.json
```

## Local Verification Script

`test_attack.py` is a local-only burst verification script restricted strictly to loopback targets (`127.0.0.1`, `localhost`, `::1`) to observe round-robin routing and `429` rate-limiting behavior:

```bash
python3 test_attack.py --url http://127.0.0.1:8080/ --count 12
```

## Development & Tests

Set up a local virtual environment for lint, type-check, and test tools:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install ruff mypy pytest
```

Run checks:

```bash
ruff check .
mypy limiter.py health.py proxy.py test_attack.py tests
pytest -v
```

## Current Limitations

- **HTTP/1.1 only:** No TLS termination or HTTP/2 support (use a TLS terminator in front if needed).
- **Buffered bodies:** Request and response bodies are buffered in memory up to `--max-body-bytes` rather than streamed.
- **Single-process state:** Rate-limiter buckets and backend health flags are stored in-process and are not shared across multiple worker processes.
- **TCP health probes:** Health checks verify TCP connectivity rather than application-level HTTP status endpoints.
