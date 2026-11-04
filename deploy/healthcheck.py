import os
import urllib.request

host = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",")[0]
request = urllib.request.Request(
    "http://127.0.0.1:8000/healthz/",
    headers={"Host": host, "X-Forwarded-Proto": "https"},
)
with urllib.request.urlopen(request, timeout=3) as response:
    if response.status != 200:
        raise SystemExit(1)
