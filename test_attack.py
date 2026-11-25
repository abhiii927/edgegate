import urllib.request
import urllib.error

print("--- Sending 12 Rapid Requests to EdgeGate ---")
for i in range(1, 13):
    try:
        res = urllib.request.urlopen("http://localhost:8080")
        served_by = res.headers.get("X-Served-By")
        print(f"Request {i:02d}: Status {res.status} | Served By: {served_by}")
    except urllib.error.HTTPError as e:
        print(f"Request {i:02d}: Status {e.code} | BLOCKED by Rate Limiter!")