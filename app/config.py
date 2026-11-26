import os
from dataclasses import dataclass
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def is_production():
    return os.getenv("APP_ENV") == "production" or bool(os.getenv("RAILWAY_ENVIRONMENT_ID"))


def database_url_from_env():
    url = os.getenv("DATABASE_URL", "").strip()
    if is_production():
        if not url:
            raise RuntimeError("DATABASE_URL is missing. Set it on the app service to your Railway Postgres connection reference.")
        if not url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            raise RuntimeError("DATABASE_URL must resolve to a PostgreSQL connection URL in production. Check the app service's Postgres variable reference.")
    return url or "sqlite:///./food-diary.db"


def garmin_sync_hours_from_env():
    raw = os.getenv("GARMIN_SYNC_HOURS", "3").strip() or "0"
    try:
        hours = float(raw)
    except ValueError:
        raise RuntimeError("GARMIN_SYNC_HOURS must be a number of hours, or 0 to turn automatic Garmin syncing off.")
    if hours and hours < 1:
        raise RuntimeError("GARMIN_SYNC_HOURS must be at least 1 (or 0 to turn it off) so Garmin isn't polled too often.")
    return hours


def int_from_env(name, default, low, high, hint, allow_zero=False):
    raw = os.getenv(name, str(default)).strip() or str(default)
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a whole number. {hint}")
    if not (low <= value <= high or (allow_zero and value == 0)):
        raise RuntimeError(f"{name} must be between {low} and {high}. {hint}")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    password: str
    session_secret: str
    base_url: str
    timezone: str = "Australia/Sydney"
    production: bool = False
    extra_redirect_uris: tuple[str, ...] = ()
    # Hours between automatic Garmin syncs of yesterday and today; 0 turns it off.
    garmin_sync_hours: float = 0
    # High-stress phone alerts: stress at or above this level (Garmin's high range starts at 76) for this many minutes,
    # checked this often while a phone has alerts on (0 turns the checks off).
    stress_alert_level: int = 76
    stress_alert_minutes: int = 30
    stress_check_minutes: int = 0

    @classmethod
    def from_env(cls):
        production = is_production()
        settings = cls(
            database_url=database_url_from_env(),
            password=os.getenv("APP_PASSWORD", ""),
            session_secret=os.getenv("SESSION_SECRET", ""),
            base_url=os.getenv("BASE_URL", "http://localhost:8000").strip().rstrip("/"),
            timezone=os.getenv("TIMEZONE", "Australia/Sydney"),
            production=production,
            extra_redirect_uris=tuple(x.strip() for x in os.getenv("OAUTH_REDIRECT_URIS", "").split(",") if x.strip()),
            garmin_sync_hours=garmin_sync_hours_from_env(),
            stress_alert_level=int_from_env("STRESS_ALERT_LEVEL", 76, 26, 100, "Garmin's high stress range starts at 76."),
            stress_alert_minutes=int_from_env("STRESS_ALERT_MINUTES", 30, 15, 240, "Use a multiple of 15, like 30."),
            stress_check_minutes=int_from_env("STRESS_CHECK_MINUTES", 30, 15, 720, "Use 0 to turn stress checks off.", allow_zero=True),
        )
        if len(settings.password) < 12 or len(settings.session_secret) < 32:
            raise RuntimeError("Set APP_PASSWORD (12+ characters) and SESSION_SECRET (32+ characters) in .env or Railway Variables.")
        parsed = urlparse(settings.base_url)
        if not parsed.hostname or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
            raise RuntimeError("BASE_URL must be the app origin, with no path or credentials.")
        if production and parsed.scheme != "https":
            raise RuntimeError("BASE_URL must use HTTPS in production. Set it to your app's public https:// address.")
        ZoneInfo(settings.timezone)
        return settings
