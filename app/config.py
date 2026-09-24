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


@dataclass(frozen=True)
class Settings:
    database_url: str
    password: str
    session_secret: str
    base_url: str
    timezone: str = "Australia/Sydney"
    production: bool = False
    extra_redirect_uris: tuple[str, ...] = ()

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
