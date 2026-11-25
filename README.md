# edgegate

A lightweight HTTP reverse proxy, load balancer, and rate limiter written in Python using only the standard library. Built to understand how Layer-7 proxies handle traffic routing, upstream failover, and basic request flood protection under the hood.

## What it does

- **Round-Robin Load Balancing:** Distributes incoming `GET` requests across active backend nodes and tags responses with `X-Served-By`.
- **Token Bucket Rate Limiter:** Tracks per-IP request bursts in memory and drops excess traffic with `429 Too Many Requests`.
- **Background Health Checks:** Probes upstream TCP ports every 2 seconds and automatically pulls dead servers out of rotation until they recover.

## How it works

```text
client (:8080)
  │
  ├─► RateLimiter (token bucket per IP) ──► 429 if empty
  ├─► HealthMonitor (TCP probe every 2s)
  │
  ├─► 127.0.0.1:9001
  └─► 127.0.0.1:9002