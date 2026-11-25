import base64
import json
import time
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

import pytest
from unittest.mock import ANY
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.garmin import ApiError, battery_events, build_timeline, summarize_activity, summarize_day
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
SYDNEY = ZoneInfo("Australia/Sydney")


def local_ms(day, hour, minute=0):
    """Epoch milliseconds for a Sydney wall-clock time on an ISO date."""
    moment = datetime.combine(date.fromisoformat(day), datetime.min.time()).replace(hour=hour, minute=minute, tzinfo=SYDNEY)
    return int(moment.timestamp() * 1000)


def day_window(day):
    """Garmin's start/end fields: local midnight in UTC and as wall-clock time."""
    start = datetime.fromtimestamp(local_ms(day, 0) / 1000, tz=timezone.utc)
    return {"startTimestampGMT": start.strftime("%Y-%m-%dT%H:%M:%S.0"), "startTimestampLocal": f"{day}T00:00:00.0"}


def heart_day(day):
    # Every 2 minutes from 06:00 to 07:58, climbing from 60 to 119 bpm, with a gap the watch left empty.
    values = [[local_ms(day, 6 + i // 30, (i % 30) * 2), 60 + i] for i in range(60)] + [[local_ms(day, 9), None]]
    return {**day_window(day), "heartRateValueDescriptors": [{"key": "timestamp", "index": 0}, {"key": "heartrate", "index": 1}],
            "heartRateValues": values, "restingHeartRate": 49}


def gmt(day, hour, minute=0):
    return datetime.fromtimestamp(local_ms(day, hour, minute) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0")


def stress_day(day):
    # Every 3 minutes from 14:00 to 14:57: 20, 23, 26 ... 77, plus Garmin's -1/-2 "no score" readings.
    values = [[local_ms(day, 14, m), 20 + m] for m in range(0, 60, 3)] + [[local_ms(day, 15, 0), -1], [local_ms(day, 15, 3), -2]]
    return {**day_window(day), "stressValueDescriptorsDTOList": [{"key": "timestamp", "index": 0}, {"key": "stressLevel", "index": 1}],
            "stressValuesArray": values}


def steps_day(day):
    # Garmin's 15-minute buckets in UTC: 06:00-06:15 local has 100 steps, 06:15 200 ... 07:45 800.
    return [{"startGMT": gmt(day, 6 + i // 4, (i % 4) * 15), "endGMT": gmt(day, 6 + (i + 1) // 4, ((i + 1) % 4) * 15),
             "steps": 100 * (i + 1), "primaryActivityLevel": "active"} for i in range(8)]


def battery_day(day):
    values = [[local_ms(day, 6, m), "MEASURED", 70 - m // 3, 3] for m in range(0, 60, 3)]
    sydney = int(datetime.fromisoformat(f"{day}T12:00").replace(tzinfo=SYDNEY).utcoffset().total_seconds() * 1000)
    events = [{"eventType": "STRESS", "eventStartTimeGmt": gmt(day, 14, 10), "timezoneOffset": sydney,
               "durationInMilliseconds": 55 * 60000, "bodyBatteryImpact": -6, "shortFeedback": "STRESSFUL_PERIOD"},
              {"eventType": "RECOVERY", "eventStartTimeGmt": gmt(day, 23, 30), "timezoneOffset": sydney,
               "durationInMilliseconds": 90 * 60000, "bodyBatteryImpact": 4}]
    return {"date": day, **day_window(day), "bodyBatteryValuesArray": values, "bodyBatteryActivityEvent": events,
            "bodyBatteryValueDescriptorDTOList": [
                {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
                {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryStatus"},
                {"bodyBatteryValueDescriptorIndex": 2, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
                {"bodyBatteryValueDescriptorIndex": 3, "bodyBatteryValueDescriptorKey": "bodyBatteryVersion"}]}


def jwt(exp, n):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'RS256'})}.{enc({'exp': exp, 'n': n})}.sig"


class FakeGarmin:
    """Stands in for personal.connectapi.garmin.com: key exchange, bearer-token GETs, failures on demand."""
    def __init__(self):
        self.valid_key, self.issued, self.revoked, self.fail = KEY, [], set(), None
        self.activities, self.calls, self.wrapped_sleep, self.empty_days = None, [], True, set()
        self.serves_stress = False  # dailyStress isn't in the Personal API's published list
        self.serves_steps = False   # nor is the 15-minute steps chart
        self.chunks_unreadable = False  # Body Battery with chunks=96 answers in a shape the parser can't read

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
        if path.startswith("/wellness-service/wellness/dailyStress/") and self.serves_stress:
            return stress_day(path.rsplit("/", 1)[1])
        if path == "/userprofile-service/userprofile/settings":
            return {"displayName": "watch-owner", "id": 1}
        if path == "/wellness-service/wellness/dailySummaryChart/watch-owner" and self.serves_steps:
            return steps_day(params["date"])
        if path == "/wellness-service/wellness/dailyHeartRate":
            return heart_day(params["date"])
        if path == "/wellness-service/wellness/bodyBattery/reports/daily":
            first, last = date.fromisoformat(params["startDate"]), date.fromisoformat(params["endDate"])
            assert (last - first).days < 31
            reports = [battery_day((first + timedelta(days=n)).isoformat()) for n in range((last - first).days + 1)]
            if "chunks" in params and self.chunks_unreadable:
                reports = [{**r, "bodyBatteryValuesArray": [["low", "high"]], "bodyBatteryValueDescriptorDTOList": []} for r in reports]
            return reports
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
    app = create_app(Settings(url, "a-private-test-password", "s" * 40, "http://localhost:8000"), garmin_http=account)
    app.state.garmin.slept = []  # pacing and back-off are recorded, not waited for
    app.state.garmin.sleep = app.state.garmin.slept.append
    return app


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


def test_history_download_runs_in_paced_weekly_batches_and_skips_finished_days(client, app, account):
    from app import garmin
    assert client.post("/api/garmin/history", json={"days": 182}).json()["detail"].startswith("Garmin isn’t connected")
    client.post("/api/garmin/key", json={"api_key": KEY})
    started = client.post("/api/garmin/history", json={"days": 182})
    assert started.status_code == 200 and started.json()["total_days"] == 182
    job = wait_for_job(app)
    assert job["state"] == "done" and job["done_days"] == 182 and job["days_synced"] == 182 and job["error"] is None
    # 182 days = 26 batches of a week, one activity search each, with a pause between batches
    # and requests spaced out within them.
    searches = [c for c in account.calls if c[0] == "/activitylist-service/activities/search/activities"]
    assert len(searches) == 26
    slept = app.state.garmin.slept
    assert slept.count(garmin.BATCH_PAUSE) == 25 and all(0 < t <= garmin.REQUEST_GAP for t in slept if t != garmin.BATCH_PAUSE)
    assert client.get("/api/garmin").json()["synced_days"]["count"] == 182
    # Downloading again only asks Garmin for days that weren't finished (today, and yesterday before 6 am).
    account.calls.clear()
    client.post("/api/garmin/history", json={"days": 182})
    job = wait_for_job(app)
    assert job["state"] == "done" and job["skipped_days"] >= 180 and job["days_synced"] + job["skipped_days"] == 182
    assert len([c for c in account.calls if c[0] == "/usersummary-service/usersummary/daily"]) == job["days_synced"] <= 2
    assert client.post("/api/garmin/history", json={"days": 800}).status_code == 422


def limited(account, calls_before, failures, retry_after=None):
    """Make Garmin answer HTTP 429 after some requests, for a number of requests (or for good)."""
    count = {"n": 0}
    original = account.get
    def get(token, path, params=None):
        count["n"] += 1
        if count["n"] > calls_before and (failures is None or count["n"] <= calls_before + failures):
            raise ApiError(429, retry_after=retry_after)
        return original(token, path, params)
    account.get = get


def test_history_download_waits_out_garmin_rate_limit(client, app, account):
    client.post("/api/garmin/key", json={"api_key": KEY})
    limited(account, calls_before=10, failures=3, retry_after=90)
    client.post("/api/garmin/history", json={"days": 14})
    job = wait_for_job(app)
    assert job["state"] == "done" and job["days_synced"] == 14 and job["waits"] == 3 and job["waiting_until"] is None
    assert [t for t in app.state.garmin.slept if t >= 20] == [90, 120, 240, 20.0]  # Retry-After, back-off, batch pause


def test_history_download_stops_when_rate_limit_persists_and_blocks_parallel_syncs(client, app, account):
    from app import garmin
    client.post("/api/garmin/key", json={"api_key": KEY})
    limited(account, calls_before=40, failures=None)
    client.post("/api/garmin/history", json={"days": 90})
    job = wait_for_job(app)
    assert job["state"] == "failed" and "still limiting" in job["error"] and "days already downloaded are skipped" in job["error"]
    assert job["waits"] == len(garmin.BACKOFF) and 0 < job["days_synced"] < 90
    page = client.get("/health").text
    assert "Garmin is limiting requests right now" in page
    # A sync someone is waiting on reports the limit straight away instead of waiting.
    assert "limiting" in client.post("/api/garmin/sync", json={}).json()["detail"]
    assert app.state.garmin.patient is False
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
    default = client.get("/health").text
    assert "of 1 day<" in default and 'value="1" checked' in default  # Health opens on today
    page = client.get("/health?period=30").text
    assert "26 of 30 days" in page and "Steps per day" in page and "11,234" in page and "49.0" in page
    assert "No watch readings · 3 days" in page  # today and the two days before collapse into one row
    assert page.count("class=\"col sel\"") == 4  # the latest worn day is selected in all four charts
    assert "Show all 30 days" in page and 'aria-current="page">Health' in page
    assert client.get("/health?start=2026-01-01&end=2026-12-31").status_code == 200
    custom = client.get(f"/health?start={day - timedelta(days=200)}&end={day}").text
    assert "of 93 days" in custom
    chosen = day - timedelta(days=10)
    assert f'id="sel-view" href="/?date={chosen}"' in client.get(f"/health?period=30&day={chosen}").text
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


def test_timeline_folds_readings_into_15_minute_local_slots():
    day = "2026-11-19"
    slots = build_timeline(date.fromisoformat(day), heart_day(day), battery_day(day), SYDNEY)
    assert [s["time"] for s in slots[:3]] == ["06:00", "06:15", "06:30"] and len(slots) == 8
    # 06:00-06:15 holds the 2-minute readings at :00, :02 ... :14 (60-67 bpm) and the last Body Battery level in it.
    assert slots[0] == {"time": "06:00", "hr": 64, "hr_min": 60, "hr_max": 67, "body_battery": 66}
    assert slots[-1]["time"] == "07:45" and "body_battery" not in slots[-1]
    # Rows without descriptors ([timestamp, level]), another day's readings and impossible values are ignored.
    battery = {**day_window(day), "bodyBatteryValuesArray": [[local_ms(day, 23, 50), 40], [local_ms("2026-11-20", 0, 5), 39],
                                                             [local_ms(day, 22), 140]]}
    assert build_timeline(date.fromisoformat(day), None, battery, SYDNEY) == [{"time": "23:45", "body_battery": 40}]
    # Travelling: the watch's own offset (UTC+9 here) decides the slot, not the diary's timezone.
    tokyo = {"startTimestampGMT": "2026-11-18T15:00:00.0", "startTimestampLocal": "2026-11-19T00:00:00.0",
             "heartRateValues": [[int(datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc).timestamp() * 1000), 70]]}
    assert build_timeline(date.fromisoformat(day), tokyo, None, SYDNEY) == [{"time": "12:00", "hr": 70, "hr_min": 70, "hr_max": 70}]


def test_one_day_shows_every_metric_every_15_minutes_and_longer_periods_day_by_day(client, app, account):
    client.post("/api/garmin/key", json={"api_key": KEY})
    day = today() - timedelta(days=1)
    client.put(f"/api/days/{day}/meals/breakfast", json={"time": "06:40", "food": "Oats", "symptom_status": "none",
                                                          "symptoms": "", "expected_version": 0})
    assert client.post("/api/garmin/sync", json={"start": str(day), "end": str(day)}).json()["warnings"] == []
    page = client.get(f"/health?start={day}&end={day}").text
    assert "Through the day" in page and "Day by day" not in page and 'id="log-heading"' not in page
    assert ">The day</h2>" in page and "<dt>Steps</dt><dd>11,234</dd><span>Goal 10,000</span>" in page
    assert "<dd>49<small> bpm</small></dd><span>Range 45–171 bpm</span>" in page and "<span>Peak 92</span>" in page
    assert [m for m in ("steps", "hr", "stress", "bb") if f'data-metric="{m}"' in page] == ["steps", "hr", "stress", "bb"]
    assert "Peak 119 bpm at 07:45 · average 90" in page and "resting 49" in page and "66 → 51" in page
    # Steps and stress levels aren't shared by this account: the day's figures show, with a note.
    assert "11,234<small> steps" in page and "Garmin only shares the day’s total for steps." in page
    assert "1 stress period, 0h 55m. Garmin only shares stress levels for the whole day." in page
    assert 'title="Breakfast 06:40"' in page and 'title="Morning Run, 06:15–06:46"' in page
    assert "<td>06:15</td><td>71 bpm</td><td>68–74</td><td>61</td>" in page
    # Longer periods chart day by day, and each day links to its 15-minute view.
    week = client.get("/health?period=7").text
    assert "Day by day" in week and 'id="timeline"' not in week and 'id="sheet-detail"' in week and 'id="log-heading"' in week
    # A day synced before 15-minute data existed asks for a sync.
    from app.models import HealthTimeline
    with app.state.sessions.begin() as db:
        db.query(HealthTimeline).delete()
    assert "No 15-minute readings for this day yet. Sync to bring them in." in client.get(f"/health?start={day}&end={day}").text
    client.post("/api/garmin/sync", json={"start": str(day), "end": str(day)})
    # Claude reads the slots, optionally only a window such as the hour after breakfast.
    token = client.post("/oauth/token", data=oauth_grant(client)).json()["access_token"]
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}
    def call(**arguments):
        response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                              "params": {"name": "get_health_timeline", "arguments": arguments}})
        return response.json()["result"]
    result = call(day=str(day), start_time="06:40", end_time="07:40")
    timeline = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert timeline["available"] and timeline["interval_minutes"] == 15
    assert [s["time"] for s in timeline["slots"]] == ["06:45", "07:00", "07:15", "07:30"]
    assert timeline["activities"] == [{"start": f"{day}T06:15", "name": "Morning Run", "type": "running", "duration_minutes": 31.0}]
    assert call(day=str(day), start_time="6pm")["isError"]
    missing = call(day=str(day - timedelta(days=3)))
    assert not (missing.get("structuredContent") or json.loads(missing["content"][0]["text"]))["available"]


def test_steps_every_15_minutes_when_garmin_serves_them(client, app, account):
    client.post("/api/garmin/key", json={"api_key": KEY})
    day = today() - timedelta(days=1)
    chart_calls = lambda: [c[0] for c in account.calls if "dailySummaryChart" in c[0]]
    # Refused: both forms of the request are tried once, then never again.
    client.post("/api/garmin/sync", json={"days": 3})
    assert app.state.garmin.details["steps"] is False and len(chart_calls()) == 2
    # The status and Settings say what Garmin answered, without the display name or any values.
    probes = client.get("/api/garmin").json()["fifteen_minute_data"]
    assert probes["steps"]["result"] == "not available" and probes["display_name"]["result"] == "found"
    assert [a["request"] for a in probes["steps"]["attempts"]] == ["/wellness-service/wellness/dailySummaryChart/{displayName}?date=…",
                                                                   "/wellness-service/wellness/dailySummaryChart?date=…"]
    assert [a["answer"] for a in probes["steps"]["attempts"]] == ["HTTP 404", "HTTP 404"]
    assert probes["stress"]["result"] == "not available" and probes["body_battery"]["result"] == "available"
    assert "rows like [int, str, int, int]" in probes["body_battery"]["attempts"][0]["answer"]
    settings = client.get("/settings").text
    assert "15-minute data from Garmin" in settings and "watch-owner" not in settings and "{displayName}" in settings
    # Served (with the display name from the profile): steps land in the slots and the chart.
    app.state.garmin.details.pop("steps"); account.serves_steps = True; account.calls.clear()
    client.post("/api/garmin/sync", json={"start": str(day), "end": str(day)})
    assert chart_calls() == ["/wellness-service/wellness/dailySummaryChart/watch-owner"]
    assert client.get("/api/garmin").json()["fifteen_minute_data"]["steps"] == {
        "result": "available", "checked_at": ANY,
        "attempts": [{"request": "/wellness-service/wellness/dailySummaryChart/{displayName}?date=…",
                      "answer": "a list of 8; first item has keys endGMT, primaryActivityLevel, startGMT, steps"}]}
    page = client.get(f"/health?start={day}&end={day}").text
    assert 'class="sbar steps"' in page and "Busiest 15 minutes 800 at 07:45" in page
    assert "<td>06:15</td><td>200</td><td>71 bpm</td>" in page


def test_body_battery_asks_for_15_minute_values_and_falls_back(client, app, account):
    client.post("/api/garmin/key", json={"api_key": KEY})
    day = today() - timedelta(days=1)
    battery_calls = lambda: [c[1] for c in account.calls if c[0].endswith("bodyBattery/reports/daily")]
    client.post("/api/garmin/sync", json={"start": str(day), "end": str(day)})
    assert battery_calls()[0]["chunks"] == 96 and app.state.garmin.battery_chunks is True
    # An unreadable answer to the 15-minute request falls back to the plain report, and isn't asked again.
    app.state.garmin.battery_chunks, account.chunks_unreadable = None, True
    account.calls.clear()
    client.post("/api/garmin/sync", json={"start": str(day), "end": str(day)})
    assert ["chunks" in c for c in battery_calls()] == [True, False] and app.state.garmin.battery_chunks is False
    assert "66 → 51" in client.get(f"/health?start={day}&end={day}").text


def test_timeline_view_model():
    from app import health_view
    slots = [{"time": "06:00", "hr": 60, "hr_min": 55, "hr_max": 70, "body_battery": 80, "steps": 400},
             {"time": "06:15", "hr": 62, "hr_min": 58, "hr_max": 66, "steps": 1200},
             {"time": "09:00", "hr": 90, "hr_min": 90, "hr_max": 90, "body_battery": 70},
             {"time": "14:00", "body_battery": 40}]
    summary = {"steps": {"total": 5016}, "heart_rate": {"resting": 52}, "stress": {"average": 20}}
    v = health_view.timeline(date(2026, 11, 19), slots, [], {"lunch": {"time": "12:30"}, "snack": {"time": None}}, (), summary)
    assert v["hr"]["line"].count("M") == 2 and v["hr"]["band"].count("Z") == 2 and v["hr"]["value"] == "55–90"
    assert v["hr"]["resting"]["label"] == "resting 52"
    # Body Battery joins readings up to 3 hours apart; 09:00 to 14:00 is too far, so 14:00 is a dot.
    assert v["bb"]["line"].count("M") == 1 and v["bb"]["dots"].count("M") == 1 and v["bb"]["value"] == "80 → 40"
    assert v["bb"]["sub"] == "High 80 at 06:00 · low 40 at 14:00"
    assert v["steps"]["value"] == "5,016" and v["steps"]["sub"] == "Busiest 15 minutes 1,200 at 06:15" and len(v["steps"]["bars"]) == 2
    assert v["stress"]["value"] == 20 and not v["stress"]["bars"]  # the day's average when there are no slots
    assert v["columns"] == {"steps": True, "stress": False}
    assert [m["label"] for m in v["meals"]] == ["Lunch 12:30"]
    assert not health_view.timeline(date(2026, 11, 19), [], [], {})["has_data"]
    with_stress = health_view.timeline(date(2026, 11, 19), [{"time": "14:00", "stress": 80, "stress_max": 90}], [], {},
                                       [{"type": "stress", "start": "14:00", "end": "15:30"}, {"type": "sleep", "start": "00:00", "end": "06:00"}])
    assert [b["band"] for b in with_stress["stress"]["bars"]] == ["high"] and [p["kind"] for p in with_stress["stress"]["periods"]] == ["stress"]
    assert with_stress["stress"]["sub"] == "Peak 90 at 14:00 · 1 stress period, 1h 30m"
    assert [health_view.stress_band(v) for v in (0, 25, 26, 51, 76)] == ["rest", "rest", "low", "medium", "high"]


def test_stress_levels_and_body_battery_events():
    day = "2026-11-19"
    slots = build_timeline(date.fromisoformat(day), None, None, SYDNEY, stress_day(day))
    # 14:00-14:15 holds 20, 23 ... 32; the negative "no score" readings at 15:00 leave no slot.
    assert slots[0] == {"time": "14:00", "stress": 26, "stress_max": 32} and [s["time"] for s in slots][-1] == "14:45"
    events = battery_events(date.fromisoformat(day), battery_day(day), SYDNEY)
    # The recovery that runs past midnight is cut at the end of the day.
    assert events == [{"type": "stress", "start": "14:10", "end": "15:05", "body_battery_impact": -6, "feedback": "STRESSFUL_PERIOD"},
                      {"type": "recovery", "start": "23:30", "end": "24:00", "body_battery_impact": 4}]
    assert battery_events(date.fromisoformat("2026-11-20"), battery_day(day), SYDNEY) == [
        {"type": "recovery", "start": "00:00", "end": "01:00", "body_battery_impact": 4}]
    assert battery_events(date.fromisoformat(day), {"bodyBatteryActivityEvent": [{"eventType": "STRESS"}, "x"]}) == []


def test_stress_detail_is_tried_once_and_used_when_garmin_serves_it(client, app, account):
    client.post("/api/garmin/key", json={"api_key": KEY})
    day = today() - timedelta(days=1)
    stress_calls = lambda: [c for c in account.calls if c[0].startswith("/wellness-service/wellness/dailyStress/")]
    # Not served: asked once, then left alone; the sync carries on and Body Battery events still show stress.
    assert client.post("/api/garmin/sync", json={"days": 3}).json()["warnings"] == []
    assert len(stress_calls()) == 1 and app.state.garmin.details["stress"] is False
    page = client.get(f"/health?start={day}&end={day}").text
    assert "1 stress period, 0h 55m" in page and "Garmin only shares stress levels for the whole day" in page
    assert 'title="Stress, 14:10–15:05, Body Battery −6"' in page and "<th scope=\"col\">Stress</th>" not in page.split("slot-table")[1]
    # Served: levels land in the slots, the chart and Claude's timeline.
    app.state.garmin.details.pop("stress"); account.serves_stress = True
    client.post("/api/garmin/sync", json={"start": str(day), "end": str(day)})
    assert app.state.garmin.details["stress"] == 0
    page = client.get(f"/health?start={day}&end={day}").text
    assert "Peak 77 at 14:45 · 1 stress period, 0h 55m" in page and 'class="sbar medium"' in page and 'class="sbar low"' in page
    assert "<td>14:00</td><td>–</td><td>–</td><td>26 (peak 32)</td><td>–</td>" in page
    with app.state.sessions() as db:
        timeline = __import__("app.garmin", fromlist=["x"]).health_timeline(db, day, "14:00", "14:30")
    assert [s["stress"] for s in timeline["slots"]] == [26, 41, 56]
    assert [e["type"] for e in timeline["events"]] == ["stress"]
    # A day without stress readings once it has worked doesn't switch it off.
    account.serves_stress = False
    client.post("/api/garmin/sync", json={"start": str(day), "end": str(day)})
    assert app.state.garmin.details["stress"] == 0
