from __future__ import annotations

import argparse
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Localhost-only EdgeGate burst test")
    parser.add_argument("--url", default="http://127.0.0.1:8080/", help="Local EdgeGate URL")
    parser.add_argument("--count", type=int, default=12, help="Number of sequential requests")
    args = parser.parse_args()

    parsed = urllib.parse.urlparse(args.url)
    if parsed.hostname not in ALLOWED_HOSTS:
        raise SystemExit(f"Refusing non-localhost target '{parsed.hostname}'. Localhost only.")

    print(f"Sending {args.count} rapid localhost requests to {args.url}...\n")
    for i in range(1, args.count + 1):
        try:
            with urllib.request.urlopen(args.url, timeout=2.0) as res:
                upstream = res.headers.get("X-Served-By")
                print(f"Request {i:02d} | Status: {res.status} OK  | Upstream: {upstream}")
        except urllib.error.HTTPError as err:
            print(f"Request {i:02d} | Status: {err.code} ERR | Blocked by Rate Limiter")


if __name__ == "__main__":
    main()