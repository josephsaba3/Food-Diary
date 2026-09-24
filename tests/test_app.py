import base64
import hashlib
import re
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    url = "sqlite:///" + (tmp_path / "diary.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    return create_app(Settings(url, "a-private-test-password", "s" * 40, "http://localhost:8000"))


def login(client):
    page = client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    response = client.post("/login", data={"password": "a-private-test-password", "csrf": csrf, "next": "/"})
    assert response.status_code == 200
    token = re.search(r'name="csrf-token" content="([^"]+)"', response.text)[1]
    client.headers["X-CSRF-Token"] = token
    return token


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://localhost:8000") as client:
        login(client)
        yield client


def meal(food="Oats", **kwargs):
    return {"food": food, "time": "08:00", "expected_version": 0, **kwargs}


def test_login_private_endpoints_and_csrf(app):
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.get("/api/days/2026-09-24").status_code == 401
        assert client.get("/export?start=2026-09-23&end=2026-09-24").status_code == 401
        assert client.post("/login", data={"password": "a-private-test-password"}).status_code == 403
        assert client.get("/", follow_redirects=False).status_code == 303
        login(client)
        client.headers.pop("X-CSRF-Token")
        assert client.put("/api/days/2026-09-24/meals/breakfast", json=meal()).status_code == 403
        assert client.get("/healthz").status_code == 200


def test_crud_and_concurrent_update(client):
    url = "/api/days/2026-09-24/meals/breakfast"
    response = client.put(url, json=meal())
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert client.put(url, json=meal("Would overwrite")).status_code == 409
    assert client.put(url, json=meal("Oats and coffee", expected_version=1)).json()["version"] == 2
    assert client.put(url, json=meal("Stale", expected_version=1)).status_code == 409
    assert client.delete(url + "?version=1").status_code == 409
    assert client.get("/api/days/2026-09-24").json()["meals"]["breakfast"]["food"] == "Oats and coffee"
    assert client.delete(url + "?version=2").status_code == 204
    assert client.get("/api/days/2026-09-24").json()["meals"] == {}


def test_yesterday_rules_only_copy_food_and_use_calendar_day(client):
    client.put("/api/days/2026-12-31/meals/breakfast", json=meal("Oats"))
    client.put("/api/days/2026-12-31/meals/dinner", json=meal("Risotto", time="19:30", symptoms="Bloating"))
    data = client.get("/api/days/2027-01-01").json()
    assert set(data["suggestions"]) == {"breakfast", "lunch", "dinner"}
    assert data["suggestions"]["breakfast"]["food"] == "Oats"
    for category in ("lunch", "dinner"):
        suggestion = data["suggestions"][category]
        assert suggestion["food"] == "Risotto"
        assert suggestion["date"] == "2026-12-31"
        assert "symptoms" not in suggestion and "time" not in suggestion
    assert data["meals"] == {}
    assert client.get("/api/days/2027-01-02").json()["suggestions"] == {}


@pytest.mark.parametrize("changes", [
    {"time": "24:00"}, {"time": "8am"}, {"food": "   "}, {"food": "a" * 5001},
    {"symptoms": "Pain", "symptom_status": "none"}, {"symptom_status": "reported"}, {"expected_version": -1}
])
def test_validation(client, changes):
    assert client.put("/api/days/2026-09-24/meals/breakfast", json=meal(**changes)).status_code == 422


def test_unknown_and_explicit_none_are_distinct(client):
    one = client.put("/api/days/2026-09-24/meals/breakfast", json=meal(time=None)).json()
    two = client.put("/api/days/2026-09-24/meals/lunch", json=meal(symptom_status="none")).json()
    assert one["symptom_status"] == "unrecorded" and one["time"] is None
    assert two["symptom_status"] == "none" and two["symptoms"] == ""


def test_notes_conflict_and_export_import_roundtrip(client):
    text = "Afternoon: mild discomfort. Severity not supplied."
    assert client.put("/api/days/2026-09-24/notes", json={"text": text, "expected_version": 0}).status_code == 200
    assert client.put("/api/days/2026-09-24/notes", json={"text": "stale", "expected_version": 0}).status_code == 409
    client.put("/api/days/2026-09-24/meals/lunch", json=meal("<script>alert(1)</script>"))
    exported = client.get("/export?start=2026-09-24&end=2026-09-24")
    assert exported.json()["days"][0]["notes"] == text
    assert client.post("/api/import", json=exported.json()).json() == {"added": 0, "skipped": 1, "notes_added": 0}
    page = client.get("/?date=2026-09-24").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert '<script>alert(1)</script>' not in page


def test_import_is_atomic_and_preserves_existing(client):
    bundle = {"days": [{"date": "2026-09-24", "meals": [
        {"category": "breakfast", "food": "Oats"}, {"category": "lunch", "food": ""}]}]}
    assert client.post("/api/import", json=bundle).status_code == 422
    assert client.get("/api/days/2026-09-24").json()["meals"] == {}
    bundle["days"][0]["meals"][1]["food"] = "Risotto"
    result = client.post("/api/import", json=bundle)
    assert result.json()["added"] == 2
    assert client.post("/api/import", json=bundle).json()["skipped"] == 2
    assert client.get("/export?start=2026-09-24&end=2026-09-23").status_code == 422


def oauth_grant(client):
    redirect = "https://chatgpt.com/connector_platform_oauth_redirect"
    reg = client.post("/oauth/register", json={"redirect_uris": [redirect], "client_name": "ChatGPT", "token_endpoint_auth_method": "none"})
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect,
              "code_challenge": challenge, "code_challenge_method": "S256", "state": "opaque-state",
              "resource": "http://localhost:8000/mcp", "scope": "diary"}
    page = client.get("/oauth/authorize", params=params)
    assert page.status_code == 200, page.text
    pending = re.search(r'name="pending" value="([^"]+)"', page.text)[1]
    result = client.post("/oauth/consent", data={"csrf": client.headers["X-CSRF-Token"], "pending": pending, "decision": "allow"}, follow_redirects=False)
    query = parse_qs(urlparse(result.headers["location"]).query)
    assert query["state"] == ["opaque-state"]
    assert query["iss"] == ["http://localhost:8000"]
    return {"grant_type": "authorization_code", "code": query["code"][0], "client_id": client_id,
            "redirect_uri": redirect, "code_verifier": verifier, "resource": params["resource"]}


def test_oauth_pkce_single_use_refresh_and_disconnect(client):
    data = oauth_grant(client)
    token = client.post("/oauth/token", data=data)
    assert token.status_code == 200, token.text
    assert client.post("/oauth/token", data=data).status_code == 400
    tokens = token.json()
    refresh = {"grant_type": "refresh_token", "client_id": data["client_id"], "resource": data["resource"], "refresh_token": tokens["refresh_token"]}
    assert client.post("/oauth/token", data=refresh).status_code == 200
    assert client.post("/oauth/token", data=refresh).status_code == 400
    assert client.post("/api/disconnect").status_code == 200
    denied = client.post("/mcp", headers={"Authorization": "Bearer " + tokens["access_token"]}, json={})
    assert denied.status_code == 401


def test_oauth_rejects_bad_callbacks_pkce_resource_and_denial(client):
    assert client.post("/oauth/register", json={"redirect_uris": ["https://evil.example/callback"]}).status_code == 400
    assert client.post("/oauth/register", json={"redirect_uris": ["https://chatgpt.com.evil.example/connector_platform_oauth_redirect"]}).status_code == 400
    data = oauth_grant(client)
    data["code_verifier"] = "incorrect" * 8
    assert client.post("/oauth/token", data=data).json()["error"] == "invalid_grant"
    data = oauth_grant(client)
    data["resource"] = "https://other.example/mcp"
    assert client.post("/oauth/token", data=data).json()["error"] == "invalid_target"


def test_oauth_metadata_denial_untrusted_redirect_and_password_change(client, app):
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["authorization_response_iss_parameter_supported"] is True
    protected = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert protected["resource"] == "http://localhost:8000/mcp"
    assert protected["authorization_servers"] == [metadata["issuer"]]
    data = oauth_grant(client)
    assert client.get("/oauth/authorize", params={"client_id": data["client_id"], "redirect_uri": "https://evil.example"}, follow_redirects=False).status_code == 400
    # A password rotation invalidates authorization codes created before it.
    app.state.oauth.security.stamp = "new-password-stamp"
    assert client.post("/oauth/token", data=data).json()["error"] == "invalid_grant"


def test_persistence_new_app_instance_and_config_guard(client, app, monkeypatch):
    client.put("/api/days/2026-09-24/meals/breakfast", json=meal("Persisted meal"))
    other = create_app(app.state.oauth.settings)
    with TestClient(other, base_url="http://localhost:8000") as second:
        login(second)
        assert second.get("/api/days/2026-09-24").json()["meals"]["breakfast"]["food"] == "Persisted meal"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_PASSWORD", "p" * 16)
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("BASE_URL", "http://localhost:8000")
    with pytest.raises(RuntimeError, match="DATABASE_URL must resolve to a PostgreSQL"):
        Settings.from_env()


def test_mcp_protocol_and_real_tools_share_ui_database(client):
    unauth = client.post("/mcp", json={})
    assert unauth.status_code == 401
    assert "oauth-protected-resource" in unauth.headers["www-authenticate"]
    token = client.post("/oauth/token", data=oauth_grant(client)).json()["access_token"]
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}
    def rpc(method, params, id=1):
        response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": id, "method": method, "params": params})
        assert response.status_code == 200, response.text
        return response.json()
    initialized = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "diary-test", "version": "1"}})
    assert initialized["result"]["serverInfo"]["name"] == "Food diary"
    listed = rpc("tools/list", {})["result"]["tools"]
    assert {t["name"] for t in listed} == {"get_diary_today", "get_diary_day", "get_diary_range", "save_diary_meal", "save_diary_notes"}
    saved = rpc("tools/call", {"name": "save_diary_meal", "arguments": {"day": "2026-09-24", "category": "breakfast", "entry": meal("MCP oats")}})
    assert not saved["result"].get("isError"), saved
    assert client.get("/api/days/2026-09-24").json()["meals"]["breakfast"]["food"] == "MCP oats"
    stale = rpc("tools/call", {"name": "save_diary_meal", "arguments": {"day": "2026-09-24", "category": "breakfast", "entry": meal("Stale oats")}})
    assert stale["result"]["isError"]
    read = rpc("tools/call", {"name": "get_diary_day", "arguments": {"day": "2026-09-24"}})
    assert not read["result"].get("isError")
