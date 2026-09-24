import os
from dataclasses import dataclass
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


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
        production = os.getenv("APP_ENV") == "production" or bool(os.getenv("RAILWAY_ENVIRONMENT_ID"))
        settings = cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./food-diary.db"),
            password=os.getenv("APP_PASSWORD", ""),
            session_secret=os.getenv("SESSION_SECRET", ""),
            base_url=os.getenv("BASE_URL", "http://localhost:8000").rstrip("/"),
            timezone=os.getenv("TIMEZONE", "Australia/Sydney"),
            production=production,
            extra_redirect_uris=tuple(x.strip() for x in os.getenv("OAUTH_REDIRECT_URIS", "").split(",") if x.strip()),
        )
        if len(settings.password) < 12 or len(settings.session_secret) < 32:
            raise RuntimeError("Set APP_PASSWORD (12+ characters) and SESSION_SECRET (32+ characters) in .env or Railway Variables.")
        parsed = urlparse(settings.base_url)
        if not parsed.hostname or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
            raise RuntimeError("BASE_URL must be the app origin, with no path or credentials.")
        if production and (parsed.scheme != "https" or not settings.database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))):
            raise RuntimeError("Production requires an HTTPS BASE_URL and PostgreSQL DATABASE_URL.")
        ZoneInfo(settings.timezone)
        return settings

