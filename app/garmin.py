"""Garmin Connect Personal API: an encrypted API key, short-lived tokens and a daily health cache.

Garmin's Personal API (developer.garmin.com/gc-developer-program/personal) lets an
owner read their own Garmin Connect data with a key created at
connect.garmin.com/app/settings/personalApiToken. The key is exchanged for a bearer
token that lasts up to 24 hours, the same flow as Garmin's official Python client.
Both are stored Fernet-encrypted with a key derived from SESSION_SECRET. Synced days
are cached in the diary database so the UI and Claude read them without calling
Garmin each time.
"""
import base64
import hashlib
import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select

from .models import GarminAccount, HealthActivity, HealthDay, utcnow

BASE_URL = os.getenv("GARMIN_PERSONAL_API_BASE_URL", "https://personal.connectapi.garmin.com").rstrip("/")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
KEY_PAGE = "https://connect.garmin.com/app/settings/personalApiToken"
TOKEN_REFRESH_WINDOW = 3600  # Get a fresh token when less than an hour remains, as Garmin's client does.
ACTIVITY_PAGE_SIZE = 100


class GarminError(RuntimeError):
    """A failure with a message that is safe and useful to show the user."""


class ApiError(Exception):
    def __init__(self, status, text=""):
        super().__init__(f"Garmin returned HTTP {status}")
        self.status, self.text = status, text


class GarminHTTP:
    """The two calls the Personal API needs: swap the key for a token, then GET with it."""
    def __init__(self, base_url=BASE_URL, timeout=30):
        self.base_url, self.timeout = base_url, timeout
        self.session = requests.Session()

    def exchange(self, api_key):
        try:
            response = self.session.post(self.base_url + TOKEN_PATH, timeout=self.timeout,
                                         headers={"Accept": "*/*", "Authorization": f"ApiKey {api_key}"})
        except requests.RequestException as error:
            raise ApiError(0, str(error)) from error
        if not response.ok:
            raise ApiError(response.status_code, response.text[:500])
        return response.text.strip()

    def get(self, token, path, params=None):
        try:
            response = self.session.get(self.base_url + path, params=params, timeout=self.timeout,
                                        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
        except requests.RequestException as error:
            raise ApiError(0, str(error)) from error
        if not response.ok:
            raise ApiError(response.status_code, response.text[:500])
        return response.json() if response.content else None


def token_expiry(token):
    """Read the JWT exp claim without verifying it (Garmin verifies; we only schedule renewal)."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return int(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return int(time.time()) + 23 * 3600


def describe(error):
    if isinstance(error, GarminError):
        return error
    status = getattr(error, "status", None)
    if status in (401, 403):
        return GarminError(f"Garmin rejected the Personal API key. It may have expired or been deleted. Create a new key at {KEY_PAGE} and paste it in Settings.")
    if status == 429:
        return GarminError("Garmin is limiting requests right now. Wait a few minutes and try again.")
    if status == 0:
        return GarminError("Couldn’t reach Garmin Connect. Try again shortly.")
    return GarminError(f"Garmin returned an unexpected response (HTTP {status}). Try again shortly.")


def is_fatal(error):
    """Errors that should stop a sync rather than skip one data set."""
    return getattr(error, "status", None) in (0, 401, 403, 429)


def trim(value, limit=40):
    """Keep raw payloads small: drop long sample arrays (heart rate every 2 minutes, sleep levels...)."""
    if isinstance(value, dict):
        return {k: trim(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [trim(v, limit) for v in value] if len(value) <= limit else f"[{len(value)} samples omitted]"
    return value


# ---- summarising Garmin payloads -------------------------------------------------

def dig(data, *path):
    for key in path:
        if isinstance(data, dict):
            data = data.get(key)
        elif isinstance(data, list) and isinstance(key, int) and -len(data) <= key < len(data):
            data = data[key]
        else:
            return None
    return data


def prune(value):
    """Drop empty values so Claude and the UI only see what the watch recorded."""
    if isinstance(value, dict):
        cleaned = {k: prune(v) for k, v in value.items()}
        return {k: v for k, v in cleaned.items() if v not in (None, {}, [], "")}
    if isinstance(value, list):
        return [prune(v) for v in value if v not in (None, {}, [], "")]
    return value


def local_stamp(ms):
    """Garmin '*TimestampLocal' values are wall-clock milliseconds expressed as if UTC."""
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")


def rounded(value, places=1):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(value) if places == 0 else round(value, places)


def summarize_day(stats=None, sleep=None, hrv=None, readiness=None, max_metrics=None):
    stats, sleep, hrv = stats or {}, sleep or {}, hrv or {}
    dto = sleep.get("dailySleepDTO") or (sleep if "sleepTimeSeconds" in sleep else {})
    hrv_summary = hrv.get("hrvSummary") or {}
    latest = None
    if isinstance(readiness, list) and readiness:
        latest = max((r for r in readiness if isinstance(r, dict)), key=lambda r: str(r.get("timestamp") or ""), default=None)
    elif isinstance(readiness, dict):
        latest = readiness
    vo2 = dig(max_metrics, 0, "generic") if isinstance(max_metrics, list) else dig(max_metrics, "generic")
    return prune({
        "steps": {"total": stats.get("totalSteps"), "goal": stats.get("dailyStepGoal"),
                  "distance_km": rounded((stats.get("totalDistanceMeters") or 0) / 1000, 2) if stats.get("totalDistanceMeters") else None,
                  "floors_climbed": rounded(stats.get("floorsAscended"), 0)},
        "calories": {"total": stats.get("totalKilocalories"), "active": stats.get("activeKilocalories"),
                     "resting": stats.get("bmrKilocalories")},
        "heart_rate": {"resting": stats.get("restingHeartRate"), "min": stats.get("minHeartRate"),
                       "max": stats.get("maxHeartRate"), "resting_7_day_avg": stats.get("lastSevenDaysAvgRestingHeartRate")},
        "intensity_minutes": {"moderate": stats.get("moderateIntensityMinutes"),
                              "vigorous": stats.get("vigorousIntensityMinutes")},
        "stress": {"average": stats.get("averageStressLevel") if (stats.get("averageStressLevel") or -1) >= 0 else None,
                   "max": stats.get("maxStressLevel") if (stats.get("maxStressLevel") or -1) >= 0 else None,
                   "rest_minutes": _minutes(stats.get("restStressDuration")),
                   "low_minutes": _minutes(stats.get("lowStressDuration")),
                   "medium_minutes": _minutes(stats.get("mediumStressDuration")),
                   "high_minutes": _minutes(stats.get("highStressDuration"))},
        "body_battery": {"highest": stats.get("bodyBatteryHighestValue"), "lowest": stats.get("bodyBatteryLowestValue"),
                         "charged": stats.get("bodyBatteryChargedValue"), "drained": stats.get("bodyBatteryDrainedValue"),
                         "at_wake": stats.get("bodyBatteryAtWakeTime"), "latest": stats.get("bodyBatteryMostRecentValue")},
        "sleep": {"start": local_stamp(dto.get("sleepStartTimestampLocal")), "end": local_stamp(dto.get("sleepEndTimestampLocal")),
                  "total_minutes": _minutes(dto.get("sleepTimeSeconds")), "deep_minutes": _minutes(dto.get("deepSleepSeconds")),
                  "light_minutes": _minutes(dto.get("lightSleepSeconds")), "rem_minutes": _minutes(dto.get("remSleepSeconds")),
                  "awake_minutes": _minutes(dto.get("awakeSleepSeconds")), "nap_minutes": _minutes(dto.get("napTimeSeconds")),
                  "score": dig(dto, "sleepScores", "overall", "value"),
                  "quality": dig(dto, "sleepScores", "overall", "qualifierKey"),
                  "avg_stress": rounded(dto.get("avgSleepStress")),
                  "avg_respiration": rounded(dto.get("averageRespirationValue")),
                  "avg_spo2": rounded(dto.get("averageSpO2Value") or dto.get("avgSpO2")),
                  "avg_overnight_hrv": rounded(sleep.get("avgOvernightHrv") or dto.get("avgOvernightHrv")),
                  "body_battery_change": sleep.get("bodyBatteryChange") or dto.get("bodyBatteryChange")},
        "hrv": {"last_night_avg": hrv_summary.get("lastNightAvg"), "last_night_5_min_high": hrv_summary.get("lastNight5MinHigh"),
                "weekly_avg": hrv_summary.get("weeklyAvg"), "status": hrv_summary.get("status"),
                "baseline_low": dig(hrv_summary, "baseline", "balancedLow"),
                "baseline_high": dig(hrv_summary, "baseline", "balancedUpper")},
        "spo2": {"average": stats.get("averageSpo2"), "lowest": stats.get("lowestSpo2")},
        "respiration": {"waking_avg": rounded(stats.get("avgWakingRespirationValue")),
                        "lowest": rounded(stats.get("lowestRespirationValue")),
                        "highest": rounded(stats.get("highestRespirationValue"))},
        "training_readiness": {"score": dig(latest, "score"), "level": dig(latest, "level"),
                               "feedback": dig(latest, "feedbackShort"),
                               "recovery_hours": rounded((dig(latest, "recoveryTime") or 0) / 60) if dig(latest, "recoveryTime") else None},
        "vo2max": rounded(dig(vo2, "vo2MaxPreciseValue") or dig(vo2, "vo2MaxValue")),
    })


def _minutes(seconds):
    return round(seconds / 60) if isinstance(seconds, (int, float)) and seconds >= 0 else None


def summarize_activity(activity):
    kind = dig(activity, "activityType", "typeKey") or "activity"
    distance = activity.get("distance") or 0
    duration = activity.get("movingDuration") or activity.get("duration") or 0
    pace = None
    if distance > 0 and duration > 0 and any(word in kind for word in ("running", "walking", "hiking")):
        per_km = duration / (distance / 1000)
        pace = f"{int(per_km // 60)}:{int(per_km % 60):02d} /km"
    start = activity.get("startTimeLocal") or ""
    return prune({
        "id": activity.get("activityId"), "date": start[:10] or None, "start": start[:16].replace(" ", "T") or None,
        "name": activity.get("activityName"), "type": kind,
        "duration_minutes": rounded((activity.get("duration") or 0) / 60) if activity.get("duration") else None,
        "moving_minutes": rounded((activity.get("movingDuration") or 0) / 60) if activity.get("movingDuration") else None,
        "distance_km": rounded(distance / 1000, 2) if distance else None, "pace": pace,
        "calories": rounded(activity.get("calories"), 0), "avg_hr": rounded(activity.get("averageHR"), 0),
        "max_hr": rounded(activity.get("maxHR"), 0), "elevation_gain_m": rounded(activity.get("elevationGain"), 0),
        "avg_cadence": rounded(activity.get("averageRunningCadenceInStepsPerMinute"), 0),
        "avg_power": rounded(activity.get("avgPower"), 0), "steps": activity.get("steps"),
        "aerobic_training_effect": rounded(activity.get("aerobicTrainingEffect")),
        "anaerobic_training_effect": rounded(activity.get("anaerobicTrainingEffect")),
        "training_effect_label": activity.get("trainingEffectLabel"),
        "training_load": rounded(activity.get("activityTrainingLoad"), 0),
        "vo2max": activity.get("vO2MaxValue"), "sets": activity.get("totalSets"), "reps": activity.get("totalReps"),
    })


# ---- reading the cache ----------------------------------------------------------

def activities_between(session, start, end):
    rows = session.scalars(select(HealthActivity).where(HealthActivity.day >= start, HealthActivity.day <= end)
                           .order_by(HealthActivity.day, HealthActivity.activity_id)).all()
    return [json.loads(row.summary) for row in rows]


def health_day(session, day, include_raw=False):
    row = session.get(HealthDay, day)
    result = {"date": day.isoformat(), "synced": row is not None,
              "synced_at": row.synced_at.isoformat() if row else None,
              "summary": json.loads(row.summary) if row else {},
              "activities": activities_between(session, day, day)}
    if include_raw:
        result["raw"] = json.loads(row.raw) if row and row.raw else {}
    return result


def health_range(session, start, end):
    rows = {r.day: r for r in session.scalars(select(HealthDay).where(HealthDay.day >= start, HealthDay.day <= end))}
    activities = {}
    for activity in activities_between(session, start, end):
        activities.setdefault(activity.get("date"), []).append(activity)
    days = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        row = rows.get(day)
        days.append({"date": day.isoformat(), "synced": row is not None,
                     "summary": json.loads(row.summary) if row else {},
                     "activities": activities.get(day.isoformat(), [])})
    return days


# ---- the link -------------------------------------------------------------------

class GarminLink:
    def __init__(self, settings, sessions, http=None):
        self.settings, self.sessions = settings, sessions
        self.http = http or GarminHTTP()
        key = hashlib.sha256(b"food-diary/garmin-tokens/" + settings.session_secret.encode()).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(key))
        self.lock = threading.Lock()

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value):
        try:
            return self.fernet.decrypt(value.encode()).decode() if value else None
        except InvalidToken:
            return None

    def today(self):
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(self.settings.timezone)).date()

    def connected(self):
        with self.sessions() as db:
            return db.get(GarminAccount, 1) is not None

    def status(self):
        with self.sessions() as db:
            account = db.get(GarminAccount, 1)
            first, last, count = db.execute(select(func.min(HealthDay.day), func.max(HealthDay.day), func.count(HealthDay.day))).one()
            return {
                "connected": account is not None,
                "key_page": KEY_PAGE,
                "connected_at": account.connected_at.isoformat() if account else None,
                "last_sync_at": account.last_sync_at.isoformat() if account and account.last_sync_at else None,
                "last_error": (account.last_error or None) if account else None,
                "synced_days": {"count": count, "first": first.isoformat() if first else None, "last": last.isoformat() if last else None},
            }

    def save_key(self, api_key):
        """Check the key by exchanging it for a token, then store both encrypted."""
        api_key = "".join((api_key or "").split())
        if api_key.lower().startswith("apikey"):
            api_key = api_key[6:]
        if len(api_key) < 16:
            raise GarminError(f"Paste the Personal API key Garmin showed when you created it at {KEY_PAGE}.")
        try:
            token = self.http.exchange(api_key)
        except ApiError as error:
            raise describe(error) from error
        with self.sessions.begin() as db:
            account = db.get(GarminAccount, 1) or GarminAccount(id=1)
            account.api_key = self.encrypt(api_key)
            account.access_token = self.encrypt(token)
            account.token_expires_at = token_expiry(token)
            account.connected_at = utcnow()
            account.last_error = ""
            db.merge(account)
        return {"connected": True}

    def disconnect(self):
        """Forget the key. Synced health days stay; they are the owner's data."""
        with self.sessions.begin() as db:
            db.execute(delete(GarminAccount))
        return {"connected": False}

    # Tokens -------------------------------------------------------------------

    def _token(self, force=False):
        with self.sessions() as db:
            account = db.get(GarminAccount, 1)
            if account is None:
                raise GarminError("Garmin isn’t connected. Add your Personal API key in the diary’s Settings first.")
            api_key, token = self.decrypt(account.api_key), self.decrypt(account.access_token)
            expires = account.token_expires_at or 0
        if not api_key:
            raise GarminError("The saved Garmin key can’t be read (the session secret changed). Paste your Personal API key again in Settings.")
        if token and not force and expires - time.time() > TOKEN_REFRESH_WINDOW:
            return token
        token = self.http.exchange(api_key)
        with self.sessions.begin() as db:
            account = db.get(GarminAccount, 1)
            if account:
                account.access_token, account.token_expires_at = self.encrypt(token), token_expiry(token)
        return token

    def _get(self, path, params=None):
        token = self._token()
        try:
            return self.http.get(token, path, params)
        except ApiError as error:
            if error.status != 401:
                raise
        # A token can be revoked before it expires; get a new one once.
        return self.http.get(self._token(force=True), path, params)

    def _record_error(self, message):
        with self.sessions.begin() as db:
            account = db.get(GarminAccount, 1)
            if account:
                account.last_error = message

    # Syncing ------------------------------------------------------------------

    def sync(self, start, end, max_days=31):
        today = self.today()
        end = min(end, today)
        if end < start:
            raise GarminError("Choose dates up to today, with the end on or after the start.")
        if (end - start).days + 1 > max_days:
            raise GarminError(f"Sync at most {max_days} days at a time.")
        if not self.lock.acquire(blocking=False):
            raise GarminError("A Garmin sync is already running. Try again in a minute.")
        try:
            return self._sync(start, end)
        finally:
            self.lock.release()

    def _optional(self, warnings, label, path, params=None):
        try:
            return self._get(path, params)
        except ApiError as error:
            if is_fatal(error):
                raise
            if error.status not in (204, 404):
                warnings.append(f"{label} unavailable (HTTP {error.status})")
            return None

    def _sync(self, start, end):
        warnings, synced, activities_synced, stopped = [], 0, None, None
        try:
            vo2_by_day = {}
            for entry in self._optional(warnings, "VO2 max", f"/metrics-service/metrics/maxmet/daily/{start}/{end}") or []:
                generic = (entry or {}).get("generic") or {}
                if generic.get("calendarDate"):
                    vo2_by_day[generic["calendarDate"]] = entry
            for offset in range((end - start).days + 1):
                day = start + timedelta(days=offset)
                iso = day.isoformat()
                stats = self._optional(warnings, f"{iso}: daily summary", "/usersummary-service/usersummary/daily", {"calendarDate": iso})
                sleep = self._optional(warnings, f"{iso}: sleep", "/sleep-service/sleep/dailySleep", {"date": iso})
                hrv = self._optional(warnings, f"{iso}: HRV", f"/hrv-service/hrv/{iso}")
                if not stats and not sleep:
                    continue
                vo2 = vo2_by_day.get(iso)
                summary = summarize_day(stats if isinstance(stats, dict) else None, sleep if isinstance(sleep, dict) else None,
                                        hrv if isinstance(hrv, dict) else None, None, [vo2] if vo2 else None)
                raw = trim({"daily_summary": stats, "sleep": sleep, "hrv": (hrv or {}).get("hrvSummary") if isinstance(hrv, dict) else hrv,
                            "vo2max": vo2})
                with self.sessions.begin() as db:
                    db.merge(HealthDay(day=day, summary=json.dumps(summary), raw=json.dumps(raw), synced_at=utcnow()))
                synced += 1
            activities = []
            for page in range(20):
                batch = self._get("/activitylist-service/activities/search/activities",
                                  {"startDate": start.isoformat(), "endDate": end.isoformat(),
                                   "start": page * ACTIVITY_PAGE_SIZE, "limit": ACTIVITY_PAGE_SIZE}) or []
                activities.extend(batch)
                if len(batch) < ACTIVITY_PAGE_SIZE:
                    break
            with self.sessions.begin() as db:
                kept = set()
                for raw in activities:
                    summary = summarize_activity(raw)
                    if not summary.get("id") or not summary.get("date"):
                        continue
                    kept.add(summary["id"])
                    db.merge(HealthActivity(activity_id=summary["id"], day=date.fromisoformat(summary["date"]),
                                            summary=json.dumps(summary), synced_at=utcnow()))
                # Activities deleted in Garmin Connect disappear here too.
                stale = delete(HealthActivity).where(HealthActivity.day >= start, HealthActivity.day <= end)
                if kept:
                    stale = stale.where(HealthActivity.activity_id.not_in(kept))
                db.execute(stale)
            activities_synced = len(kept)
        except (ApiError, GarminError) as error:
            stopped = describe(error)
            self._record_error(str(stopped))
            if not synced:
                raise stopped from error
        if stopped is None:
            with self.sessions.begin() as db:
                account = db.get(GarminAccount, 1)
                if account:
                    account.last_sync_at, account.last_error = utcnow(), ""
        return {"start": start.isoformat(), "end": end.isoformat(), "days_synced": synced,
                "activities_synced": activities_synced, "stopped_early": str(stopped) if stopped else None,
                "warnings": warnings[:20]}
