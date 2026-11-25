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

from .models import GarminAccount, HealthActivity, HealthDay, HealthTimeline, utcnow

BASE_URL = os.getenv("GARMIN_PERSONAL_API_BASE_URL", "https://personal.connectapi.garmin.com").rstrip("/")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
KEY_PAGE = "https://connect.garmin.com/app/settings/personalApiToken"
TOKEN_REFRESH_WINDOW = 3600  # Get a fresh token when less than an hour remains, as Garmin's client does.
ACTIVITY_PAGE_SIZE = 100
CHUNK_DAYS = 31        # Longer downloads run as a background job in chunks of this size.
MAX_HISTORY_DAYS = 731 # Two years.
SLOT_MINUTES = 15     # Heart rate and Body Battery are kept through the day in slots this long.


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


# ---- through the day: heart rate and Body Battery in 15-minute slots -------------

def utc_moment(value):
    """Garmin timestamps arrive as epoch milliseconds, zone-less ISO strings in UTC, or {"date": "..."} objects."""
    if isinstance(value, dict):
        value = value.get("date")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return None


def wall_clock(value):
    """A '*TimestampLocal' string as naive wall-clock time."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return None


def local_clock(payload, tz):
    """Map UTC moments to the watch's wall clock for one day.

    The response's start times give the watch's UTC offset that day. When it matches the diary's
    timezone, that zone is used so a daylight-saving change mid-day lands correctly; otherwise
    (travelling) the watch's fixed offset is used.
    """
    start_utc = utc_moment((payload or {}).get("startTimestampGMT"))
    start_local = wall_clock((payload or {}).get("startTimestampLocal"))
    if start_utc and start_local:
        offset = start_local - start_utc.replace(tzinfo=None)
        if start_utc.astimezone(tz).utcoffset() != offset:
            return lambda moment: (moment + offset).replace(tzinfo=None)
    return lambda moment: moment.astimezone(tz).replace(tzinfo=None)


def value_rows(payload, values_key, descriptors_key, index_key, name_key):
    """Yield each sample row as {column name: value}, using Garmin's column descriptors."""
    columns = {}
    for item in (payload or {}).get(descriptors_key) or []:
        if isinstance(item, dict) and isinstance(item.get(index_key), int) and item.get(name_key):
            columns[str(item[name_key])] = item[index_key]
    for row in (payload or {}).get(values_key) or []:
        if isinstance(row, list):
            yield row, {name: row[i] if 0 <= i < len(row) else None for name, i in columns.items()}


def _number(value, low, high):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high else None


def build_timeline(day, heart=None, battery=None, tz=timezone.utc, stress=None):
    """Fold Garmin's heart rate, Body Battery and stress readings into 15-minute slots of the watch's local day.

    Each slot has the average, lowest and highest heart rate, the last Body Battery reading and the average
    and highest stress level (0-100; Garmin's negative 'no score' readings are skipped) in it.
    Slots with no readings are left out.
    """
    slots = {}

    def slot(moment, clock):
        if moment is None:
            return None
        local = clock(moment)
        if local.date() != day:
            return None
        return slots.setdefault((local.hour * 60 + local.minute) // SLOT_MINUTES, {"hr": [], "bb": [], "stress": []})

    if isinstance(heart, dict):
        clock = local_clock(heart, tz)
        for row, named in value_rows(heart, "heartRateValues", "heartRateValueDescriptors", "index", "key"):
            stamp = named.get("timestamp", row[0] if row else None)
            bpm = _number(named.get("heartrate", row[1] if len(row) > 1 else None), 20, 250)
            target = slot(utc_moment(stamp), clock) if bpm is not None else None
            if target is not None:
                target["hr"].append(bpm)
    if isinstance(battery, dict):
        clock = local_clock(battery, tz)
        readings = []
        for row, named in value_rows(battery, "bodyBatteryValuesArray", "bodyBatteryValueDescriptorDTOList",
                                     "bodyBatteryValueDescriptorIndex", "bodyBatteryValueDescriptorKey"):
            stamp = named.get("timestamp", row[0] if row else None)
            if "bodyBatteryLevel" in named:
                level = named["bodyBatteryLevel"]
            else:  # undescribed rows are [timestamp, level] or [timestamp, status, level, version]
                level = row[2] if len(row) >= 4 else row[1] if len(row) > 1 else None
            level, moment = _number(level, 0, 100), utc_moment(stamp)
            if level is not None and moment is not None:
                readings.append((moment, level))
        for moment, level in sorted(readings):
            target = slot(moment, clock)
            if target is not None:
                target["bb"].append(level)
    if isinstance(stress, dict):
        clock = local_clock(stress, tz)
        for row, named in value_rows(stress, "stressValuesArray", "stressValueDescriptorsDTOList", "index", "key"):
            stamp = named.get("timestamp", row[0] if row else None)
            level = _number(named.get("stressLevel", row[1] if len(row) > 1 else None), 0, 100)
            target = slot(utc_moment(stamp), clock) if level is not None else None
            if target is not None:
                target["stress"].append(level)
    out = []
    for index in sorted(slots):
        hr, bb, levels = slots[index]["hr"], slots[index]["bb"], slots[index]["stress"]
        if not hr and not bb and not levels:
            continue
        minutes = index * SLOT_MINUTES
        entry = {"time": f"{minutes // 60:02d}:{minutes % 60:02d}"}
        if hr:
            entry.update(hr=round(sum(hr) / len(hr)), hr_min=round(min(hr)), hr_max=round(max(hr)))
        if bb:
            entry["body_battery"] = round(bb[-1])
        if levels:
            entry.update(stress=round(sum(levels) / len(levels)), stress_max=round(max(levels)))
        out.append(entry)
    return out


def battery_events(day, battery, tz=timezone.utc):
    """Garmin's timed Body Battery events (stress, recovery, activity, sleep, nap) that fall on the day, in local time."""
    events = []
    start_of_day = datetime.combine(day, datetime.min.time())
    end_of_day = start_of_day + timedelta(days=1)

    def clock(moment):
        return "24:00" if moment >= end_of_day else moment.strftime("%H:%M")

    found = battery.get("bodyBatteryActivityEvent") if isinstance(battery, dict) else None
    for event in found if isinstance(found, list) else []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("eventType") or "").lower()
        start = utc_moment(event.get("eventStartTimeGmt"))
        length = _number(event.get("durationInMilliseconds"), 0, 2 * 86400000)
        if not kind or start is None or not length:
            continue
        offset = _number(event.get("timezoneOffset"), -16 * 3600000, 16 * 3600000)
        local = (start + timedelta(milliseconds=offset)).replace(tzinfo=None) if offset is not None else start.astimezone(tz).replace(tzinfo=None)
        finish = local + timedelta(milliseconds=length)
        if finish <= start_of_day or local >= end_of_day:
            continue
        events.append(prune({"type": kind, "start": clock(max(local, start_of_day)), "end": clock(min(finish, end_of_day)),
                             "body_battery_impact": event.get("bodyBatteryImpact"),
                             "feedback": event.get("shortFeedback")}))
    return sorted(events, key=lambda e: e["start"])

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
                     "synced_at": row.synced_at.isoformat() if row else None,
                     "summary": json.loads(row.summary) if row else {},
                     "activities": activities.get(day.isoformat(), [])})
    return days


def health_timeline(session, day, start=None, end=None):
    """A day's 15-minute slots, optionally only those starting between two HH:MM times (inclusive)."""
    row = session.get(HealthTimeline, day)
    slots = json.loads(row.slots) if row else []
    if start:
        slots = [s for s in slots if s["time"] >= start]
    if end:
        slots = [s for s in slots if s["time"] <= end]
    events = json.loads(row.events or "[]") if row else []
    if start:
        events = [e for e in events if e["end"] > start]
    if end:
        events = [e for e in events if e["start"] <= end]
    return {"date": day.isoformat(), "available": row is not None,
            "synced_at": row.synced_at.isoformat() if row else None,
            "interval_minutes": SLOT_MINUTES, "slots": slots, "events": events}

# ---- the link -------------------------------------------------------------------

class GarminLink:
    def __init__(self, settings, sessions, http=None):
        self.settings, self.sessions = settings, sessions
        self.http = http or GarminHTTP()
        key = hashlib.sha256(b"food-diary/garmin-tokens/" + settings.session_secret.encode()).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(key))
        self.lock = threading.Lock()
        self.stress_supported = None  # unknown until Garmin answers a dailyStress request

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value):
        try:
            return self.fernet.decrypt(value.encode()).decode() if value else None
        except InvalidToken:
            return None

    def zone(self):
        from zoneinfo import ZoneInfo
        return ZoneInfo(self.settings.timezone)

    def today(self):
        return datetime.now(self.zone()).date()

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
                "history_download": self.job_status(),
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

    def _stress_detail(self, iso):
        """Stress levels through the day, if Garmin serves them to a Personal API key.

        dailyStress isn't in the Personal API's published list, so it is tried once per process; any refusal
        (other than a network failure or rate limit) means stop asking. A day without readings after it has
        worked is just an empty day.
        """
        if getattr(self, "stress_supported", None) is False:
            return None
        try:
            data = self.http.get(self._token(), f"/wellness-service/wellness/dailyStress/{iso}")
        except ApiError as error:
            if error.status in (0, 429):
                raise
            if not (self.stress_supported and error.status in (204, 404)):
                self.stress_supported = False
            return None
        if isinstance(data, dict) and isinstance(data.get("stressValuesArray"), list):
            self.stress_supported = True
            return data
        return None

    def _sync(self, start, end, on_day=None):
        warnings, synced, activities_synced, stopped = [], 0, None, None
        try:
            vo2_by_day = {}
            for entry in self._optional(warnings, "VO2 max", f"/metrics-service/metrics/maxmet/daily/{start}/{end}") or []:
                generic = (entry or {}).get("generic") or {}
                if generic.get("calendarDate"):
                    vo2_by_day[generic["calendarDate"]] = entry
            battery_by_day = {}
            piece = start
            while piece <= end:  # Garmin serves Body Battery reports for at most 31 days per request.
                piece_end = min(end, piece + timedelta(days=30))
                for report in self._optional(warnings, "Body Battery", "/wellness-service/wellness/bodyBattery/reports/daily",
                                             {"startDate": piece.isoformat(), "endDate": piece_end.isoformat()}) or []:
                    if isinstance(report, dict) and report.get("date"):
                        battery_by_day[str(report["date"])[:10]] = report
                piece = piece_end + timedelta(days=1)
            tz = self.zone()
            for offset in range((end - start).days + 1):
                day = start + timedelta(days=offset)
                iso = day.isoformat()
                stats = self._optional(warnings, f"{iso}: daily summary", "/usersummary-service/usersummary/daily", {"calendarDate": iso})
                sleep = self._optional(warnings, f"{iso}: sleep", "/sleep-service/sleep/dailySleep", {"date": iso})
                hrv = self._optional(warnings, f"{iso}: HRV", f"/hrv-service/hrv/{iso}")
                if not stats and not sleep:
                    if on_day:
                        on_day()
                    continue
                heart = self._optional(warnings, f"{iso}: heart rate", "/wellness-service/wellness/dailyHeartRate", {"date": iso})
                stress = self._stress_detail(iso)
                slots = build_timeline(day, heart, battery_by_day.get(iso), tz, stress)
                events = battery_events(day, battery_by_day.get(iso), tz)
                vo2 = vo2_by_day.get(iso)
                summary = summarize_day(stats if isinstance(stats, dict) else None, sleep if isinstance(sleep, dict) else None,
                                        hrv if isinstance(hrv, dict) else None, None, [vo2] if vo2 else None)
                raw = trim({"daily_summary": stats, "sleep": sleep, "hrv": (hrv or {}).get("hrvSummary") if isinstance(hrv, dict) else hrv,
                            "vo2max": vo2})
                with self.sessions.begin() as db:
                    db.merge(HealthDay(day=day, summary=json.dumps(summary), raw=json.dumps(raw), synced_at=utcnow()))
                    if slots or events:
                        db.merge(HealthTimeline(day=day, slots=json.dumps(slots, separators=(",", ":")),
                                                events=json.dumps(events, separators=(",", ":")), synced_at=utcnow()))
                    else:
                        db.execute(delete(HealthTimeline).where(HealthTimeline.day == day))
                synced += 1
                if on_day:
                    on_day()
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

    # Background history download -------------------------------------------

    def job_status(self):
        return dict(self.job) if getattr(self, "job", None) else {"state": "idle"}

    def start_job(self, start, end):
        """Download a long range (up to two years) in 31-day chunks, newest first, on a background thread."""
        end = min(end, self.today())
        if end < start:
            raise GarminError("Choose dates up to today, with the end on or after the start.")
        total = (end - start).days + 1
        if total > MAX_HISTORY_DAYS:
            raise GarminError("Download at most two years at a time.")
        if not self.connected():
            raise GarminError("Garmin isn’t connected. Add your Personal API key in the diary’s Settings first.")
        if not self.lock.acquire(blocking=False):
            raise GarminError("A Garmin sync is already running. Try again in a minute.")
        self.job = {"state": "running", "start": start.isoformat(), "end": end.isoformat(), "total_days": total,
                    "done_days": 0, "days_synced": 0, "activities_synced": 0, "error": None, "warnings": 0,
                    "started_at": utcnow().isoformat(), "finished_at": None}
        self.job_thread = threading.Thread(target=self._run_job, args=(start, end), daemon=True, name="garmin-history")
        self.job_thread.start()
        return self.job_status()

    def _run_job(self, start, end):
        job = self.job

        def tick():
            job["done_days"] += 1

        try:
            chunk_end = end
            while chunk_end >= start:
                chunk_start = max(start, chunk_end - timedelta(days=CHUNK_DAYS - 1))
                done_before = job["done_days"]
                result = self._sync(chunk_start, chunk_end, on_day=tick)
                job["done_days"] = done_before + (chunk_end - chunk_start).days + 1
                job["days_synced"] += result["days_synced"]
                job["activities_synced"] += result["activities_synced"] or 0
                job["warnings"] += len(result["warnings"])
                if result["stopped_early"]:
                    job["error"] = result["stopped_early"]
                    break
                chunk_end = chunk_start - timedelta(days=1)
        except GarminError as error:
            job["error"] = str(error)
        except Exception as error:  # keep the thread's failure visible instead of silent
            job["error"] = f"The download stopped unexpectedly ({type(error).__name__}). Try again."
        finally:
            job["state"] = "failed" if job["error"] else "done"
            job["finished_at"] = utcnow().isoformat()
            self.lock.release()
