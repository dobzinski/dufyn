"""Single-process manager sessions. Background API polling never extends a session."""
import hashlib
import hmac
import os
import secrets
import time
from threading import Lock
from urllib.parse import urlencode, urlsplit

from fastapi.responses import JSONResponse, RedirectResponse

COOKIE = "dufyn_session"


class AccessControl:
    def __init__(self, get_config):
        self.get_config = get_config
        self.sessions = {}
        self.attempts = {}
        self.lock = Lock()
        self.username = os.environ.get("MANAGER_LOGIN_USER", "")
        password = os.environ.get("MANAGER_LOGIN_PASSWORD", "")
        self.password = hashlib.sha256(password.encode()).digest()
        self.configured = bool(self.username and password and not password.startswith("REPLACE_WITH_"))

    @property
    def enabled(self):
        return self.get_config().get("access", {}).get("login", False)

    @property
    def timeout(self):
        return self.get_config().get("access", {}).get("session", 3600)

    def valid(self, request, touch=False):
        token = request.cookies.get(COOKIE)
        now = time.monotonic()
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if now - value < self.timeout}
            if token not in self.sessions:
                return False
            if touch:
                self.sessions[token] = now
            return True

    def same_origin(self, request):
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return False
        return not origin or origin == str(request.base_url).rstrip("/")

    async def middleware(self, request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not self.same_origin(request):
            return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
        if not self.enabled:
            return await call_next(request)
        path = request.url.path
        public = path in {"/login", "/health", "/ready"} or path.startswith("/static/")
        if not public and not self.valid(request, touch=request.method == "GET" and not path.startswith("/api/") and path != "/auth/status"):
            if path.startswith(("/api/", "/auth/")):
                return JSONResponse({"detail": "Authentication required"}, status_code=401, headers={"Cache-Control": "no-store"})
            return RedirectResponse("/login?" + urlencode({"next": path + ("?" + request.url.query if request.url.query else "")}), status_code=303)
        response = await call_next(request)
        if not path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def destination(self, value):
        # Only allow local absolute paths; browsers normalize backslashes as slashes.
        if not value or not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value):
            return "/"
        parsed = urlsplit(value)
        return "/" if parsed.netloc or parsed.scheme or parsed.path == "/login" else value

    def authenticate(self, request, username, password):
        now = time.monotonic()
        address = request.client.host if request.client else "unknown"
        with self.lock:
            self.attempts = {key: value for key, value in self.attempts.items() if now - value[0] < 60}
            started, count = self.attempts.get(address, (now, 0))
            if count >= 10 or len(self.attempts) >= 10000:
                return None, 429
            self.attempts[address] = (started, count + 1)
            user_ok = hmac.compare_digest(username.encode(), self.username.encode())
            password_ok = hmac.compare_digest(hashlib.sha256(password.encode()).digest(), self.password)
            if not self.configured or not (user_ok and password_ok):
                return None, 401
            self.sessions = {key: value for key, value in self.sessions.items() if now - value < self.timeout}
            if len(self.sessions) >= 10000:
                return None, 429
            self.sessions.pop(request.cookies.get(COOKIE), None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now
            self.attempts.pop(address, None)
            return token, 200

    def logout(self, request):
        with self.lock:
            self.sessions.pop(request.cookies.get(COOKIE), None)
