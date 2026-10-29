import json
from urllib.request import urlopen
from app.controller_tls import request_tls_options


def controller_available(url, ca_file=None):
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        with urlopen(url.rstrip("/") + "/health", timeout=3, **request_tls_options(url, ca_file)) as response:
            payload = json.loads(response.read(4096))
            return response.status == 200 and isinstance(payload, dict) and payload.get("status") == "ok"
    except (OSError, ValueError):
        return False
