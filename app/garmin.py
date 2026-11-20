"""Garmin Connect link: encrypted tokens, MFA hand-off and a daily health cache.

Garmin offers no personal API keys, so this signs in the way the Garmin Connect
app does (via the garminconnect library). The Garmin password is used once and
never stored; only the refreshable session tokens are kept, encrypted with a key
derived from SESSION_SECRET. Synced days are cached in the diary database so the
UI and Claude read them without calling Garmin each time.
"""
import base64
import hashlib
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select

from .models import AuthRecord, GarminAccount, HealthActivity, HealthDay, utcnow

MFA_KEY = "garmin-mfa"
MFA_TTL = 600


class GarminError(RuntimeError):
    """A failure with a message that is safe and useful to show the user."""


def default_client_factory(**kwargs):
    from garminconnect import Garmin
    return Garmin(**kwargs)


def _garmin_errors():
    try:
        from garminconnect import (GarminConnectAuthenticationError, GarminConnectConnectionError,
                                   GarminConnectTooManyRequestsError)
        return GarminConnectAuthenticationError, GarminConnectConnectionError, GarminConnectTooManyRequestsError
    except ImportError:  # pragma: no cover - the library is a requirement
        return (), (), ()


def friendly(error):
    auth, connection, too_many = _garmin_errors()
    if isinstance(error, GarminError):
        return error
    if auth and isinstance(error, auth):
        return GarminError("Garmin didn’t accept those details. Check your email, password or code and try again.")
    if too_many and isinstance(error, too_many):
        return GarminError("Garmin is limiting requests right now. Wait a few minutes and try again.")
    if connection and isinstance(error, connection):
        return GarminError("Couldn’t reach Garmin Connect. Try again shortly.")
    return GarminError("Garmin returned an unexpected response. Try again shortly.")


def is_auth_error(error):
    auth = _garmin_errors()[0]
    return bool(auth) and isinstance(error, auth)


def is_rate_limit(error):
    too_many = _garmin_errors()[2]
    return bool(too_many) and isinstance(error, too_many)


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
    dto = sleep.get("dailySleepDTO") or {}
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
                  "avg_overnight_hrv": rounded(sleep.get("avgOvernightHrv")),
                  "body_battery_change": sleep.get("bodyBatteryChange")},
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


def health_day(session, day):
    row = session.get(HealthDay, day)
    return {"date": day.isoformat(), "synced": row is not None,
            "synced_at": row.synced_at.isoformat() if row else None,
            "summary": json.loads(row.summary) if row else {},
            "activities": activities_between(session, day, day)}


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
    def __init__(self, settings, sessions, client_factory=None):
        self.settings, self.sessions = settings, sessions
        self.client_factory = client_factory or default_client_factory
        key = hashlib.sha256(b"food-diary/garmin-tokens/" + settings.session_secret.encode()).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(key))
        self.lock = threading.Lock()

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value):
        try:
            return self.fernet.decrypt(value.encode()).decode()
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
            pending = db.get(AuthRecord, MFA_KEY)
            return {
                "connected": account is not None,
                "name": account.full_name or account.display_name if account else None,
                "connected_at": account.connected_at.isoformat() if account else None,
                "last_sync_at": account.last_sync_at.isoformat() if account and account.last_sync_at else None,
                "last_error": account.last_error or None if account else None,
                "awaiting_mfa_code": bool(pending and pending.expires_at > time.time()),
                "synced_days": {"count": count, "first": first.isoformat() if first else None, "last": last.isoformat() if last else None},
            }

    # Connecting ---------------------------------------------------------------

    def start_login(self, email, password):
        email, password = (email or "").strip(), password or ""
        if not email or not password:
            raise GarminError("Enter the email and password you use for Garmin Connect.")
        client = self.client_factory(email=email, password=password, return_on_mfa=True)
        try:
            result = client.login()
        except Exception as error:
            raise friendly(error) from error
        if isinstance(result, tuple) and result and result[0] == "needs_mfa":
            with self.sessions.begin() as db:
                db.execute(delete(AuthRecord).where(AuthRecord.key == MFA_KEY))
                db.add(AuthRecord(key=MFA_KEY, kind="garmin_mfa", payload=self.encrypt(json.dumps(result[1])),
                                  expires_at=int(time.time()) + MFA_TTL))
            return {"status": "needs_mfa"}
        self._save_account(client)
        return {"status": "connected"}

    def finish_mfa(self, code):
        code = (code or "").strip().replace(" ", "")
        if not code:
            raise GarminError("Enter the code Garmin sent you.")
        with self.sessions() as db:
            pending = db.get(AuthRecord, MFA_KEY)
            state = self.decrypt(pending.payload) if pending and pending.expires_at > time.time() else None
        if not state:
            raise GarminError("That sign-in expired. Start again with your Garmin email and password.")
        client = self.client_factory()
        try:
            client.resume_login(json.loads(state), code)
        except Exception as error:
            raise friendly(error) from error
        self._save_account(client)
        return {"status": "connected"}

    def import_tokens(self, tokens):
        tokens = (tokens or "").strip()
        try:
            data = json.loads(tokens)
        except ValueError:
            data = None
        if not isinstance(data, dict) or not data.get("di_token") or not data.get("di_refresh_token"):
            raise GarminError("Paste the whole garmin_tokens.json file. It contains di_token and di_refresh_token.")
        client = self.client_factory()
        try:
            client.login(tokens)
        except Exception as error:
            raise friendly(error) from error
        self._save_account(client)
        return {"status": "connected"}

    def _save_account(self, client):
        with self.sessions.begin() as db:
            db.execute(delete(AuthRecord).where(AuthRecord.key == MFA_KEY))
            account = db.get(GarminAccount, 1) or GarminAccount(id=1, connected_at=utcnow())
            account.tokens = self.encrypt(client.client.dumps())
            account.display_name = getattr(client, "display_name", None)
            account.full_name = getattr(client, "full_name", None)
            account.connected_at = utcnow()
            account.last_error = ""
            db.merge(account)

    def disconnect(self):
        """Forget Garmin tokens. Synced health days stay; they are the owner's data."""
        with self.sessions.begin() as db:
            db.execute(delete(AuthRecord).where(AuthRecord.key == MFA_KEY))
            db.execute(delete(GarminAccount))
        return {"connected": False}

    # Syncing ------------------------------------------------------------------

    def _client(self):
        with self.sessions() as db:
            account = db.get(GarminAccount, 1)
            tokens = self.decrypt(account.tokens) if account else None
        if account is None:
            raise GarminError("Garmin isn’t connected. Connect it in the diary’s Settings first.")
        if not tokens:
            raise GarminError("The saved Garmin sign-in can’t be read (the session secret changed). Reconnect Garmin in Settings.")
        client = self.client_factory()
        try:
            client.login(tokens)
        except Exception as error:
            self._record_error(error)
            raise friendly(error) from error
        return client, tokens

    def _record_error(self, error):
        message = ("Garmin signed the diary out. Reconnect Garmin in Settings." if is_auth_error(error)
                   else str(friendly(error)))
        with self.sessions.begin() as db:
            account = db.get(GarminAccount, 1)
            if account:
                account.last_error = message

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

    def _sync(self, start, end):
        client, tokens = self._client()
        warnings, synced, stopped = [], 0, None
        try:
            for offset in range((end - start).days + 1):
                day = start + timedelta(days=offset)
                iso = day.isoformat()
                parts = {}
                for name, call in (("stats", lambda: client.get_user_summary(iso)), ("sleep", lambda: client.get_sleep_data(iso)),
                                   ("hrv", lambda: client.get_hrv_data(iso)), ("readiness", lambda: client.get_training_readiness(iso)),
                                   ("max_metrics", lambda: client.get_max_metrics(iso))):
                    try:
                        parts[name] = call()
                    except Exception as error:
                        if is_auth_error(error) or is_rate_limit(error):
                            raise
                        warnings.append(f"{iso}: {name.replace('_', ' ')} unavailable")
                if "stats" not in parts and "sleep" not in parts:
                    continue
                summary = summarize_day(**parts)
                with self.sessions.begin() as db:
                    db.merge(HealthDay(day=day, summary=json.dumps(summary), synced_at=utcnow()))
                synced += 1
            activities = client.get_activities_by_date(start.isoformat(), end.isoformat()) or []
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
        except Exception as error:
            self._record_error(error)
            stopped = friendly(error)
            if not synced:
                raise stopped from error
        finally:
            self._persist_tokens(client, tokens, error=stopped)
        return {"start": start.isoformat(), "end": end.isoformat(), "days_synced": synced,
                "activities_synced": len(kept) if stopped is None else None,
                "stopped_early": str(stopped) if stopped else None, "warnings": warnings[:20]}

    def _persist_tokens(self, client, previous, error=None):
        try:
            current = client.client.dumps()
        except Exception:
            current = previous
        with self.sessions.begin() as db:
            account = db.get(GarminAccount, 1)
            if account is None:
                return
            if current and current != previous:
                account.tokens = self.encrypt(current)
            if error is None:
                account.last_sync_at = utcnow()
                account.last_error = ""
