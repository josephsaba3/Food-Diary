import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

from app.config import Settings
from app.garmin import summarize_activity, summarize_day
from app.main import create_app
from app.models import GarminAccount
from tests.test_app import login, oauth_grant

TOKENS = {"di_token": "access-1", "di_refresh_token": "refresh-1", "di_client_id": "client"}
STATS = {"totalSteps": 11234, "dailyStepGoal": 10000, "totalDistanceMeters": 8650.0, "totalKilocalories": 2650.0,
         "activeKilocalories": 720.0, "restingHeartRate": 49, "minHeartRate": 45, "maxHeartRate": 171,
         "averageStressLevel": 28, "maxStressLevel": 92, "highStressDuration": 1800, "moderateIntensityMinutes": 20,
         "vigorousIntensityMinutes": 35, "bodyBatteryHighestValue": 88, "bodyBatteryLowestValue": 21,
         "bodyBatteryChargedValue": 61, "bodyBatteryDrainedValue": 70, "averageSpo2": 96, "floorsAscended": 7.0}
SLEEP = {"dailySleepDTO": {"sleepTimeSeconds": 27120, "deepSleepSeconds": 5400, "lightSleepSeconds": 14400,
         "remSleepSeconds": 6000, "awakeSleepSeconds": 1320, "sleepStartTimestampLocal": 1790375400000,
         "sleepEndTimestampLocal": 1790403900000, "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}}},
         "avgOvernightHrv": 54.0, "bodyBatteryChange": 58}
HRV = {"hrvSummary": {"lastNightAvg": 54, "weeklyAvg": 51, "status": "BALANCED", "baseline": {"balancedLow": 45, "balancedUpper": 60}}}
RUN = {"activityId": 21000000001, "activityName": "Morning Run", "activityType": {"typeKey": "running"},
       "startTimeLocal": "{day} 06:15:00", "duration": 1860.0, "movingDuration": 1800.0, "distance": 6000.0,
       "calories": 410.0, "averageHR": 152.0, "maxHR": 171.0, "aerobicTrainingEffect": 3.4, "trainingEffectLabel": "TEMPO"}


class FakeAccount:
    """Stands in for Garmin Connect: one account, optional MFA, token refresh on use."""
    def __init__(self):
        self.mfa, self.tokens, self.fail = False, dict(TOKENS), None
        self.activities = None

    def factory(self, **kwargs):
        return FakeGarmin(self, **kwargs)


class FakeSession:
    def __init__(self):
        self.tokens = None

    def dumps(self):
        return json.dumps(self.tokens)


class FakeGarmin:
    def __init__(self, account, email=None, password=None, return_on_mfa=False, **kwargs):
        self.account, self.email, self.password, self.return_on_mfa = account, email, password, return_on_mfa
        self.client, self.full_name, self.display_name = FakeSession(), None, None

    def _signed_in(self):
        self.client.tokens = dict(self.account.tokens)
        self.full_name, self.display_name = "Joe Runner", "joe-runner"

    def login(self, tokenstore=None):
        if tokenstore:
            if json.loads(tokenstore) != self.account.tokens:
                raise GarminConnectAuthenticationError("stale")
            self._signed_in()
            return None, None
        if self.password != "garmin-pass":
            raise GarminConnectAuthenticationError("bad password")
        if self.account.mfa and self.return_on_mfa:
            return "needs_mfa", {"cookies": "opaque"}
        self._signed_in()
        return None, None

    def resume_login(self, state, code):
        if state != {"cookies": "opaque"} or code != "123456":
            raise GarminConnectAuthenticationError("bad code")
        self._signed_in()
        return None, None

    def _call(self, value):
        if self.account.fail:
            raise self.account.fail
        # Garmin rotates tokens now and then; the diary must keep the newest pair.
        self.account.tokens = {**self.account.tokens, "di_token": "access-2"}
        self.client.tokens = dict(self.account.tokens)
        return value

    def get_user_summary(self, d): return self._call(STATS)
    def get_sleep_data(self, d): return self._call(SLEEP)
    def get_hrv_data(self, d): return self._call(HRV)
    def get_training_readiness(self, d): return self._call([{"timestamp": "2026-11-19T06:00", "score": 71, "level": "HIGH"}])
    def get_max_metrics(self, d): raise ValueError("no VO2 max today")

    def get_activities_by_date(self, start, end):
        if self.account.activities is not None:
            return self._call(self.account.activities)
        return self._call([{**RUN, "startTimeLocal": RUN["startTimeLocal"].format(day=start)}])


@pytest.fixture
def account():
    return FakeAccount()


@pytest.fixture
def app(tmp_path, monkeypatch, account):
    url = "sqlite:///" + (tmp_path / "diary.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    return create_app(Settings(url, "a-private-test-password", "s" * 40, "http://localhost:8000"), garmin_client_factory=account.factory)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://localhost:8000") as client:
        login(client)
        yield client


def today():
    return datetime.now(ZoneInfo("Australia/Sydney")).date()


def test_summaries_keep_only_recorded_values():
    day = summarize_day(STATS, SLEEP, HRV, [{"timestamp": "a", "score": 40}, {"timestamp": "b", "score": 71, "level": "HIGH"}], None)
    assert day["steps"] == {"total": 11234, "goal": 10000, "distance_km": 8.65, "floors_climbed": 7}
    assert day["sleep"]["total_minutes"] == 452 and day["sleep"]["score"] == 82 and day["sleep"]["start"].endswith("T22:30")
    assert day["body_battery"]["highest"] == 88 and day["hrv"]["status"] == "BALANCED"
    assert day["training_readiness"] == {"score": 71, "level": "HIGH"}
    assert "vo2max" not in day and "respiration" not in day
    assert summarize_day({"averageStressLevel": -1}) == {}
    run = summarize_activity({**RUN, "startTimeLocal": "2026-11-19 06:15:00"})
    assert run["pace"] == "5:00 /km" and run["distance_km"] == 6.0 and run["start"] == "2026-11-19T06:15"


def test_link_with_password_and_mfa_never_stores_password(client, app, account):
    account.mfa = True
    assert client.post("/api/garmin/login", json={"email": "joe@example.com", "password": "wrong"}).status_code == 400
    assert client.post("/api/garmin/login", json={"email": "joe@example.com", "password": "garmin-pass"}).json() == {"status": "needs_mfa"}
    assert client.get("/api/garmin").json()["awaiting_mfa_code"] is True
    assert client.post("/api/garmin/mfa", json={"code": "000000"}).status_code == 400
    assert client.post("/api/garmin/mfa", json={"code": "123456"}).json() == {"status": "connected"}
    status = client.get("/api/garmin").json()
    assert status["connected"] and status["name"] == "Joe Runner" and not status["awaiting_mfa_code"]
    with app.state.sessions() as db:
        stored = db.get(GarminAccount, 1).tokens
    assert "garmin-pass" not in stored and "refresh-1" not in stored
    assert json.loads(app.state.garmin.decrypt(stored)) == TOKENS
    assert "Joe Runner" in client.get("/settings").text


def test_garmin_writes_need_csrf(client):
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/garmin/login", json={"email": "joe@example.com", "password": "garmin-pass"}).status_code == 403
    assert client.post("/api/garmin/sync", json={}).status_code == 403


def test_token_paste_sync_refresh_and_diary_page(client, app, account):
    assert client.post("/api/garmin/tokens", json={"tokens": '{"oops": 1}'}).status_code == 400
    assert client.post("/api/garmin/tokens", json={"tokens": json.dumps(TOKENS)}).json() == {"status": "connected"}
    day = today()
    result = client.post("/api/garmin/sync", json={"days": 2}).json()
    assert result["days_synced"] == 2 and result["activities_synced"] == 1
    assert any("max metrics" in w for w in result["warnings"])
    with app.state.sessions() as db:
        assert json.loads(app.state.garmin.decrypt(db.get(GarminAccount, 1).tokens))["di_token"] == "access-2"
    health = client.get(f"/api/health/{day - timedelta(days=1)}").json()
    assert health["synced"] and health["summary"]["heart_rate"]["resting"] == 49
    assert health["activities"][0]["name"] == "Morning Run"
    page = client.get(f"/?date={day - timedelta(days=1)}").text
    assert "From your Garmin" in page and "11,234" in page and "7h 32m" in page and "Morning Run" in page
    # A workout deleted in Garmin Connect disappears on the next sync of that range.
    account.activities = []
    client.post("/api/garmin/sync", json={"days": 2})
    assert client.get(f"/api/health/{day - timedelta(days=1)}").json()["activities"] == []


def test_sync_errors_are_reported_and_disconnect_keeps_data(client, account):
    assert client.post("/api/garmin/sync", json={}).json()["detail"].startswith("Garmin isn’t connected")
    client.post("/api/garmin/tokens", json={"tokens": json.dumps(TOKENS)})
    assert client.post("/api/garmin/sync", json={"start": "2026-01-01", "end": "2026-03-01"}).status_code == 200
    assert client.post("/api/garmin/sync", json={"days": 93}).status_code == 422
    account.fail = GarminConnectTooManyRequestsError("slow down")
    assert "limiting" in client.post("/api/garmin/sync", json={}).json()["detail"]
    account.fail = None
    account.tokens = {**account.tokens, "di_refresh_token": "revoked"}
    assert client.post("/api/garmin/sync", json={}).status_code == 400
    assert "Reconnect" in client.get("/api/garmin").json()["last_error"]
    assert client.post("/api/garmin/disconnect").json() == {"connected": False}
    status = client.get("/api/garmin").json()
    assert not status["connected"] and status["synced_days"]["count"] >= 30


def test_mcp_health_tools(client, account):
    token = client.post("/oauth/token", data=oauth_grant(client)).json()["access_token"]
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}
    def call(name, **arguments):
        response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                              "params": {"name": name, "arguments": arguments}})
        assert response.status_code == 200, response.text
        return response.json()["result"]
    def data(result):
        assert not result.get("isError"), result
        return result.get("structuredContent", {}).get("result") or [json.loads(c["text"]) for c in result["content"]]
    assert call("sync_garmin", start="2026-11-01")["isError"]
    client.post("/api/garmin/tokens", json={"tokens": json.dumps(TOKENS)})
    day = (today() - timedelta(days=1)).isoformat()
    synced = call("sync_garmin", start=day)
    assert data(synced)[0]["days_synced"] == 1
    assert data(call("get_health_day", day=day))[0]["summary"]["sleep"]["score"] == 82
    assert data(call("get_health_range", start=day, end=day))[0]["synced"]
    assert data(call("get_activities", start=day, end=day))[0]["type"] == "running"
    assert call("get_health_range", start="2026-01-01", end="2026-03-01")["isError"]
    assert data(call("get_garmin_status"))[0]["connected"]
