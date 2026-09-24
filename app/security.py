import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class Security:
    def __init__(self, settings):
        self.settings = settings
        self.attempts = defaultdict(deque)
        self.stamp = hmac.new(settings.session_secret.encode(), settings.password.encode(), hashlib.sha256).hexdigest()

    def authenticated(self, request):
        return hmac.compare_digest(str(request.session.get("user", "")), self.stamp)

    def require_user(self, request: Request):
        if not self.authenticated(request):
            raise HTTPException(401, "Your session expired. Sign in again; your open form is still here.")

    def csrf(self, request):
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(32)
        return request.session["csrf"]

    def check_csrf(self, request, token):
        expected = request.session.get("csrf")
        if not expected or not hmac.compare_digest(str(token or ""), expected):
            raise HTTPException(403, "This page expired. Reload it and try again.")

    def check_write(self, request: Request):
        self.require_user(request)
        self.check_csrf(request, request.headers.get("X-CSRF-Token"))

    def rate_limit(self, request, action, limit=12, window=300):
        now = time.monotonic()
        # Bound this single-process limiter. One worker is used by the deployment.
        if len(self.attempts) > 5000:
            self.attempts = defaultdict(deque, {k: v for k, v in self.attempts.items() if v and now - v[-1] < window})
        key = (request.client.host if request.client else "unknown", action)
        attempts = self.attempts[key]
        while attempts and attempts[0] < now - window:
            attempts.popleft()
        if len(attempts) >= limit:
            raise HTTPException(429, "Too many attempts. Please wait five minutes and try again.", headers={"Retry-After": str(window)})
        attempts.append(now)

    def password_ok(self, password):
        return hmac.compare_digest(password.encode(), self.settings.password.encode())


def local_next(value):
    return value if value.startswith("/") and not value.startswith("//") and "\\" not in value and "\r" not in value and "\n" not in value else "/"
