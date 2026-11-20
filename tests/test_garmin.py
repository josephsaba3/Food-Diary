import base64
import json
import time
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.garmin import ApiError, summarize_activity, summarize_day
from app.main import create_app
from app.models import GarminAccount
from tests.test_app import login, oauth_grant

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


KEY = "gcpak-0123456789abcdef0123456789abcdef"


def jwt(exp, n):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'RS256'})}.{enc({'exp': exp, 'n': n})}.sig"


class FakeGarmin:
    """Stands in for personal.connectapi.garmin.com: key exchange, bearer-token GETs, failures on demand."""
    def __init__(self):
        self.valid_key, self.issued, self.revoked, self.fail = KEY, [], set(), None
        self.activities, self.calls, self.wrapped_sleep, self.empty_days = None, [], True, set()

    def exchange(self, api_key):
        if api_key != self.valid_key:
            raise ApiError(401, "invalid key")
        token = jwt(int(time.time()) + 86400, len(self.issued))
        self.issued.append(token)
        return token

    def get(self, token, path, params=None):
        self.calls.append((path, params))
        if token not in self.issued or token in self.revoked:
            raise ApiError(401)
        if self.fail:
            raise self.fail
        if path == "/usersummary-service/usersummary/daily":
            assert set(params) == {"calendarDate"}
            return None if params["calendarDate"] in self.empty_days else STATS
        if path == "/sleep-service/sleep/dailySleep":
            if params["date"] in self.empty_days:
                return None
            return SLEEP if self.wrapped_sleep else {**SLEEP["dailySleepDTO"], "avgOvernightHrv": 54.0}
        if path.startswith("/hrv-service/hrv/"):
            return HRV
        if path.startswith("/metrics-service/metrics/maxmet/daily/"):
            raise ApiError(404)
        if path == "/activitylist-service/activities/search/activities":
            if self.activities is not None:
                return self.activities[params["start"]:params["start"] + params["limit"]]
            run = {**RUN, "activityId": RUN["activityId"] + date.fromisoformat(params["startDate"]).toordinal(),
                   "startTimeLocal": RUN["startTimeLocal"].format(day=params["startDate"])}
            return [run] if params["start"] == 0 else []
        raise ApiError(404)


@pytest.fixture
def account():
    return FakeGarmin()


@pytest.fixture
def app(tmp_path, monkeypatch, account):
    url = "sqlite:///" + (tmp_path / "diary.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    return create_app(Settings(url, "a-private-test-password", "s" * 40, "http://localhost:8000"), garmin_http=account)


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


def test_link_with_personal_api_key_stores_it_encrypted(client, app, account):
    assert client.post("/api/garmin/key", json={"api_key": "short"}).status_code == 400
    rejected = client.post("/api/garmin/key", json={"api_key": "gcpak-wrong-key-0000000000000000"})
    assert rejected.status_code == 400 and "rejected" in rejected.json()["detail"]
    assert client.post("/api/garmin/key", json={"api_key": f"  ApiKey {KEY}\n"}).json() == {"connected": True}
    with app.state.sessions() as db:
        stored = db.get(GarminAccount, 1)
        assert KEY not in stored.api_key and app.state.garmin.decrypt(stored.api_key) == KEY
        assert stored.token_expires_at > time.time() + 3600
    page = client.get("/settings").text
    assert "Linked with your Garmin Connect Personal API key" in page and KEY not in page


def test_garmin_writes_need_csrf(client):
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/garmin/key", json={"api_key": KEY}).status_code == 403
    assert client.post("/api/garmin/sync", json={}).status_code == 403


def test_sync_reuses_and_renews_tokens_and_fills_the_diary(client, app, account):
    client.post("/api/garmin/key", json={"api_key": KEY})
    day = today()
    account.wrapped_sleep = False
    result = client.post("/api/garmin/sync", json={"days": 2}).json()
    assert result["days_synced"] == 2 and result["activities_synced"] == 1 and result["warnings"] == []
    assert len(account.issued) == 1  # the token from linking is reused
    # A token revoked early is replaced once, transparently.
    account.revoked.add(account.issued[-1])
    assert client.post("/api/garmin/sync", json={}).json()["days_synced"] == 1
    assert len(account.issued) == 2
    health = client.get(f"/api/health/{day - timedelta(days=1)}").json()
    assert health["synced"] and health["summary"]["heart_rate"]["resting"] == 49
    assert health["summary"]["sleep"]["score"] == 82 and health["summary"]["sleep"]["avg_overnight_hrv"] == 54
    assert health["activities"][0]["name"] == "Morning Run"
    page = client.get(f"/?date={day - timedelta(days=1)}").text
    assert "From your Garmin" in page and "11,234" in page and "7h 32m" in page and "Morning Run" in page
    # A workout deleted in Garmin Connect disappears on the next sync of that range.
    account.activities = []
    client.post("/api/garmin/sync", json={"days": 2})
    assert client.get(f"/api/health/{day - timedelta(days=1)}").json()["activities"] == []


def test_activity_pages_are_followed(client, account):
    client.post("/api/garmin/key", json={"api_key": KEY})
    account.activities = [{**RUN, "activityId": 1000 + i, "startTimeLocal": f"{today()} 06:{i % 60:02d}:00"} for i in range(150)]
    assert client.post("/api/garmin/sync", json={}).json()["activities_synced"] == 150


def test_sync_errors_are_reported_and_disconnect_keeps_data(client, account):
    assert client.post("/api/garmin/sync", json={}).json()["detail"].startswith("Garmin isn’t connected")
    client.post("/api/garmin/key", json={"api_key": KEY})
    assert client.post("/api/garmin/sync", json={"start": "2026-01-01", "end": "2026-03-01"}).status_code == 200
    assert client.post("/api/garmin/sync", json={"days": 93}).status_code == 422
    account.fail = ApiError(429)
    assert "limiting" in client.post("/api/garmin/sync", json={}).json()["detail"]
    account.fail = ApiError(500)
    assert "HTTP 500" in client.post("/api/garmin/sync", json={}).json()["detail"]
    account.fail = None
    # The key is deleted or expires in Garmin Connect.
    account.valid_key = "another"
    account.revoked.update(account.issued)
    assert client.post("/api/garmin/sync", json={}).status_code == 400
    assert "Personal API key" in client.get("/api/garmin").json()["last_error"]
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
    client.post("/api/garmin/key", json={"api_key": KEY})
    day = (today() - timedelta(days=1)).isoformat()
    synced = call("sync_garmin", start=day)
    assert data(synced)[0]["days_synced"] == 1
    assert data(call("get_health_day", day=day))[0]["summary"]["sleep"]["score"] == 82
    raw = data(call("get_health_day", day=day, include_raw=True))[0]["raw"]
    assert raw["daily_summary"]["totalSteps"] == 11234 and raw["hrv"]["status"] == "BALANCED"
    assert data(call("get_health_range", start=day, end=day))[0]["synced"]
    assert data(call("get_activities", start=day, end=day))[0]["type"] == "running"
    assert call("get_health_range", start="2026-01-01", end="2026-03-01")["isError"]
    assert data(call("get_garmin_status"))[0]["connected"]


def wait_for_job(app):
    app.state.garmin.job_thread.join(timeout=10)
    return app.state.garmin.job_status()


def test_history_download_runs_in_background_chunks(client, app, account):
    assert client.post("/api/garmin/history", json={"days": 182}).json()["detail"].startswith("Garmin isn’t connected")
    client.post("/api/garmin/key", json={"api_key": KEY})
    started = client.post("/api/garmin/history", json={"days": 182})
    assert started.status_code == 200 and started.json()["total_days"] == 182
    job = wait_for_job(app)
    assert job["state"] == "done" and job["done_days"] == 182 and job["days_synced"] == 182 and job["error"] is None
    # 182 days = six 31-day chunks (the last one shorter), one activity search per chunk.
    searches = [c for c in account.calls if c[0] == "/activitylist-service/activities/search/activities"]
    assert len(searches) == 6
    assert client.get("/api/garmin").json()["synced_days"]["count"] == 182
    assert client.post("/api/garmin/history", json={"days": 800}).status_code == 422


def test_history_download_stops_on_rate_limit_and_blocks_parallel_syncs(client, app, account):
    client.post("/api/garmin/key", json={"api_key": KEY})
    calls = {"n": 0}
    original = account.get
    def slow_then_limited(token, path, params=None):
        calls["n"] += 1
        if calls["n"] > 40:
            raise ApiError(429)
        return original(token, path, params)
    account.get = slow_then_limited
    client.post("/api/garmin/history", json={"days": 90})
    job = wait_for_job(app)
    assert job["state"] == "failed" and "limiting" in job["error"] and 0 < job["days_synced"] < 90
    page = client.get("/health").text
    assert "Garmin is limiting requests right now" in page
    app.state.garmin.lock.acquire()
    try:
        assert "already running" in client.post("/api/garmin/history", json={"days": 7}).json()["detail"]
    finally:
        app.state.garmin.lock.release()


def test_health_page_states(client, app, account):
    page = client.get("/health").text
    assert "Link your Garmin to see trends" in page and 'href="/settings#garmin"' in page
    client.post("/api/garmin/key", json={"api_key": KEY})
    empty = client.get("/health?period=7").text
    assert "0 of 7 days" in empty and "No watch readings for these days" in empty
    day = today()
    account.empty_days = {(day - timedelta(days=n)).isoformat() for n in (0, 1, 2, 5)}
    client.post("/api/garmin/history", json={"days": 30})
    wait_for_job(app)
    page = client.get("/health").text
    assert "26 of 30 days" in page and "Steps per day" in page and "11,234" in page and "49.0" in page
    assert "No watch readings · 3 days" in page  # today and the two days before collapse into one row
    assert page.count("class=\"col sel\"") == 4  # the latest worn day is selected in all four charts
    assert "Show all 30 days" in page and 'aria-current="page">Health' in page
    assert client.get("/health?start=2026-01-01&end=2026-12-31").status_code == 200
    custom = client.get(f"/health?start={day - timedelta(days=200)}&end={day}").text
    assert "of 93 days" in custom
    chosen = day - timedelta(days=10)
    assert f'id="sel-view" href="/?date={chosen}"' in client.get(f"/health?day={chosen}").text
    diary = client.get(f"/?date={day - timedelta(days=3)}").text
    assert "See how this compares over 30 days" in diary


def test_health_view_model():
    from app import health_view
    start = date(2026, 11, 1)
    days = []
    for n in range(10):
        d = start + timedelta(days=n)
        worn = n not in (3, 4, 5, 9)
        summary = {"steps": {"total": 1000 * (n + 1)}, "heart_rate": {"resting": 60 + n},
                   "stress": {"average": 20 + n}, "body_battery": {"highest": 80, "lowest": 10 + n}} if worn else {}
        days.append({"date": d.isoformat(), "summary": summary, "activities": [], "synced_at": None})
    v = health_view.build(days, start, start + timedelta(days=9), start + timedelta(days=9))
    assert v["worn_count"] == 6 and v["selected"] == 8
    assert v["averages"]["steps"] == "5,000" and v["averages"]["rhr_range"] == "60–68 bpm"
    assert v["averages"]["bb_low"] == "10 at its low" and v["averages"]["stress_peak"] == "28 · 9 Nov"
    titles = [r["title"] for r in v["rows"]]
    assert titles[0] == "Tue 10 Nov" and "4–6 Nov" in titles and len(v["rows"]) == 8
    assert v["charts"]["rhr_path"].count("M") == 2  # the line breaks across the unworn days
    assert health_view.resolve_period(date(2026, 11, 20), start=date(2026, 1, 1), end=date(2026, 12, 1))[0] == date(2026, 11, 20) - timedelta(days=92)
