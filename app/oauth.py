"""Single-owner OAuth: explicit consent, PKCE, persistent one-use codes and rotation.

No public sign-up. Clients can register only trusted callback URLs. Tokens are
opaque random values; only their hashes are stored. All grants are diary-scoped.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import delete, select

from .models import AuthRecord

SCOPE = "diary"


def token_key(value):
    return hashlib.sha256(value.encode()).hexdigest()


class OAuth:
    def __init__(self, settings, sessions, security, templates):
        self.settings, self.sessions, self.security, self.templates = settings, sessions, security, templates
        self.issuer = settings.base_url
        self.resource = self.issuer + "/mcp"

    def put(self, kind, value, payload, ttl, session=None):
        record = AuthRecord(key=token_key(value), kind=kind, payload=json.dumps(payload), expires_at=int(time.time()) + ttl)
        if session is not None:
            session.add(record)
        else:
            with self.sessions.begin() as db:
                db.execute(delete(AuthRecord).where(AuthRecord.expires_at < int(time.time())))
                db.add(record)

    def get(self, kind, value):
        with self.sessions() as db:
            item = db.get(AuthRecord, token_key(value))
            if item and item.kind == kind and item.expires_at > time.time():
                return json.loads(item.payload)
        return None

    def consume(self, db, kind, value):
        item = db.execute(delete(AuthRecord).where(AuthRecord.key == token_key(value), AuthRecord.kind == kind,
            AuthRecord.expires_at > int(time.time())).returning(AuthRecord.payload)).scalar_one_or_none()
        return json.loads(item) if item else None

    def allowed_redirect(self, value):
        parsed = urlparse(value)
        if parsed.fragment or parsed.username or parsed.password:
            return False
        if value in self.settings.extra_redirect_uris:
            return parsed.scheme == "https" or (not self.settings.production and parsed.hostname in ("localhost", "127.0.0.1"))
        return value == "https://chatgpt.com/connector_platform_oauth_redirect" or bool(
            re.fullmatch(r"https://chatgpt\.com/aip/[a-zA-Z0-9_-]+/oauth/callback", value))

    def access_valid(self, token):
        data = self.get("access", token)
        return bool(data and data.get("resource") == self.resource and data.get("stamp") == self.security.stamp)

    def issue(self, db, client_id, resource):
        access, refresh = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        payload = {"client_id": client_id, "resource": resource, "stamp": self.security.stamp}
        self.put("access", access, payload, 3600, db)
        self.put("refresh", refresh, payload, 30 * 86400, db)
        return {"access_token": access, "token_type": "Bearer", "expires_in": 3600,
                "refresh_token": refresh, "scope": SCOPE}

    def install(self, app):
        @app.get("/.well-known/oauth-protected-resource")
        @app.get("/.well-known/oauth-protected-resource/mcp")
        def resource_metadata():
            return {"resource": self.resource, "authorization_servers": [self.issuer], "scopes_supported": [SCOPE]}

        @app.get("/.well-known/oauth-authorization-server")
        def metadata():
            return {"issuer": self.issuer, "authorization_endpoint": self.issuer + "/oauth/authorize",
                    "token_endpoint": self.issuer + "/oauth/token", "registration_endpoint": self.issuer + "/oauth/register",
                    "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
                    "scopes_supported": [SCOPE], "authorization_response_iss_parameter_supported": True}

        @app.post("/oauth/register")
        async def register(request: Request):
            self.security.rate_limit(request, "register", limit=20)
            try:
                body = await request.json()
            except ValueError:
                return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
            redirects = body.get("redirect_uris") if isinstance(body, dict) else None
            if (not isinstance(redirects, list) or not 1 <= len(redirects) <= 5
                    or not all(isinstance(uri, str) and self.allowed_redirect(uri) for uri in redirects)
                    or body.get("token_endpoint_auth_method", "none") != "none"):
                return JSONResponse({"error": "invalid_client_metadata", "error_description": "Use an allowed callback and public-client PKCE (token_endpoint_auth_method: none)."}, status_code=400)
            client = secrets.token_urlsafe(32)
            record = {"client_id": client, "client_name": str(body.get("client_name", "ChatGPT"))[:100],
                      "redirect_uris": redirects, "token_endpoint_auth_method": "none",
                      "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": SCOPE}
            self.put("client", client, record, 365 * 86400)
            return JSONResponse(record, status_code=201)

        @app.get("/oauth/authorize")
        def authorize(request: Request):
            params = dict(request.query_params)
            client = self.get("client", params.get("client_id", ""))
            redirect = params.get("redirect_uri", "")
            # Never redirect errors to an unvalidated URL.
            if not client or redirect not in client["redirect_uris"] or not self.allowed_redirect(redirect):
                raise HTTPException(400, "Unknown OAuth client or callback URL.")
            valid = (params.get("response_type") == "code" and params.get("code_challenge_method") == "S256"
                     and re.fullmatch(r"[A-Za-z0-9_-]{43}", params.get("code_challenge", ""))
                     and params.get("resource") == self.resource
                     and params.get("scope", SCOPE) == SCOPE and len(params.get("state", "")) <= 1000)
            if not valid:
                return self.callback(params, error="invalid_request")
            if not self.security.authenticated(request):
                next_path = "/oauth/authorize?" + urlencode(params)
                return RedirectResponse("/login?" + urlencode({"next": next_path}), status_code=303)
            pending = secrets.token_urlsafe(32)
            params["csrf_binding"] = self.security.csrf(request)
            params["stamp"] = self.security.stamp
            self.put("pending", pending, params, 600)
            return self.templates.TemplateResponse(request, "connect.html", {"client_name": client["client_name"],
                "callback_host": urlparse(redirect).hostname, "pending": pending, "csrf": self.security.csrf(request)})

        @app.post("/oauth/consent")
        async def consent(request: Request):
            self.security.require_user(request)
            form = await request.form()
            self.security.check_csrf(request, form.get("csrf"))
            with self.sessions.begin() as db:
                params = self.consume(db, "pending", str(form.get("pending", "")))
                if not params or not hmac.compare_digest(params["csrf_binding"], self.security.csrf(request)):
                    raise HTTPException(400, "Connection request expired. Start again from ChatGPT.")
                if form.get("decision") != "allow":
                    return self.callback(params, error="access_denied")
                code = secrets.token_urlsafe(48)
                self.put("code", code, params, 120, db)
            return self.callback(params, code=code)

        @app.post("/oauth/token")
        async def token(request: Request):
            self.security.rate_limit(request, "token", limit=120)
            form = await request.form()
            client_id = str(form.get("client_id", ""))
            if not self.get("client", client_id):
                return JSONResponse({"error": "invalid_client"}, status_code=400)
            resource = str(form.get("resource", ""))
            if resource != self.resource:
                return JSONResponse({"error": "invalid_target"}, status_code=400)
            with self.sessions.begin() as db:
                if form.get("grant_type") == "authorization_code":
                    data = self.consume(db, "code", str(form.get("code", "")))
                    verifier = str(form.get("code_verifier", ""))
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                    valid = (data and data["client_id"] == client_id and data["resource"] == resource
                             and data.get("stamp") == self.security.stamp
                             and data["redirect_uri"] == form.get("redirect_uri")
                             and re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
                             and hmac.compare_digest(challenge, data["code_challenge"]))
                elif form.get("grant_type") == "refresh_token":
                    data = self.consume(db, "refresh", str(form.get("refresh_token", "")))
                    valid = (data and data["client_id"] == client_id and data["resource"] == resource
                             and data["stamp"] == self.security.stamp and form.get("scope", SCOPE) == SCOPE)
                else:
                    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
                if not valid:
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                response = self.issue(db, client_id, resource)
            return JSONResponse(response, headers={"Cache-Control": "no-store"})

    def callback(self, params, **result):
        result["iss"] = self.issuer
        if "state" in params:
            result["state"] = params["state"]
        url = params["redirect_uri"]
        return RedirectResponse(url + ("&" if "?" in url else "?") + urlencode(result), status_code=303)


class MCPAuthorization:
    def __init__(self, app, oauth):
        self.app, self.oauth = app, oauth

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            auth = headers.get(b"authorization", b"").decode("latin1")
            token = auth[7:] if auth.lower().startswith("bearer ") else ""
            # Keep synchronous database I/O off the MCP event loop.
            from starlette.concurrency import run_in_threadpool
            if not token or not await run_in_threadpool(self.oauth.access_valid, token):
                response = JSONResponse({"error": "unauthorized"}, status_code=401, headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{self.oauth.issuer}/.well-known/oauth-protected-resource", scope="diary"'})
                return await response(scope, receive, send)
        await self.app(scope, receive, send)
