"""Optional Manager update checks against the project's public version file."""
import os
import re
import ssl
from threading import Lock
import time
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


LATEST_URL = "https://raw.githubusercontent.com/dobzinski/dufyn/main/releases/latest.txt"
RELEASE_URL = "https://github.com/dobzinski/dufyn/releases/tag/v{}"
SUCCESS_CACHE_SECONDS = 6 * 60 * 60


def version_parts(value):
    match = re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value)
    if match is None:
        raise ValueError("Expected a stable major.minor.patch version")
    return tuple(int(part) for part in match.groups())


class UpdateChecker:
    def __init__(self):
        self.latest_url = os.environ.get("MANAGER_UPDATE_URL", LATEST_URL)
        parsed = urlsplit(self.latest_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("MANAGER_UPDATE_URL must be an HTTPS URL without credentials")
        try:
            self.success_cache_seconds = int(os.environ.get("MANAGER_UPDATE_CACHE_SECONDS", str(SUCCESS_CACHE_SECONDS)))
        except ValueError:
            raise ValueError("MANAGER_UPDATE_CACHE_SECONDS must be a nonnegative integer") from None
        if self.success_cache_seconds < 0:
            raise ValueError("MANAGER_UPDATE_CACHE_SECONDS must be a nonnegative integer")
        self.lock = Lock()
        self.expires_at = 0
        self.cached = None

    def check(self, installed_version, enabled):
        if not enabled:
            return {"status": "disabled"}
        with self.lock:
            if self.cached and self.cached["installed_version"] == installed_version and time.monotonic() < self.expires_at:
                return dict(self.cached)
            result = {"status": "error", "installed_version": installed_version, "check_url": self.latest_url}
            lifetime = 0
            try:
                installed = version_parts(installed_version)
                request = Request(self.latest_url, headers={"Accept": "text/plain", "User-Agent": "DUFYN/" + installed_version})
                # This opener uses standard proxy/CA settings without changing the global opener.
                opener = build_opener(ProxyHandler(), HTTPSHandler(context=ssl.create_default_context()))
                with opener.open(request, timeout=5) as response:
                    payload = response.read(65)
                    if response.status != 200 or len(payload) > 64:
                        raise ValueError("Invalid version response")
                latest = version_parts(payload.decode("utf-8-sig").strip())
                latest_version = ".".join(str(part) for part in latest)
                result.update(status="available" if latest > installed else "current",
                              latest_version=latest_version, release_url=RELEASE_URL.format(latest_version))
                lifetime = self.success_cache_seconds
            except (OSError, ValueError):
                pass
            self.cached = result if lifetime else None
            self.expires_at = time.monotonic() + lifetime
            return dict(result)
