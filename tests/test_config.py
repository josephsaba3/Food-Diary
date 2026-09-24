import pytest
from alembic import command
from alembic.config import Config

from app.config import Settings, database_url_from_env


@pytest.fixture
def railway_env(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "test-environment")
    monkeypatch.setenv("APP_PASSWORD", "test-password-long-enough")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("BASE_URL", "https://diary.example")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@postgres.railway.internal/railway")


@pytest.mark.parametrize("value", [None, "", "sqlite:///./food-diary.db", "${{Postgres.DATABASE_PRIVATE_URL}}"])
def test_railway_rejects_missing_or_unresolved_database(railway_env, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("DATABASE_URL")
    else:
        monkeypatch.setenv("DATABASE_URL", value)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings.from_env()


def test_https_error_identifies_base_url(railway_env, monkeypatch):
    monkeypatch.setenv("BASE_URL", "http://localhost:8000")
    with pytest.raises(RuntimeError, match="BASE_URL must use HTTPS") as error:
        Settings.from_env()
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_production_accepts_postgres_schemes_and_trims_whitespace(railway_env, monkeypatch, scheme):
    url = f"{scheme}://user:secret@postgres.railway.internal/railway"
    monkeypatch.setenv("DATABASE_URL", f" {url}\n")
    monkeypatch.setenv("BASE_URL", " https://diary.example/\n")
    settings = Settings.from_env()
    assert settings.production is True
    assert settings.database_url == url
    assert settings.base_url == "https://diary.example"


@pytest.mark.parametrize("value", [None, "sqlite:///./food-diary.db"])
def test_migration_rejects_sqlite_on_railway_before_opening_database(railway_env, monkeypatch, value):
    from app import database

    if value is None:
        monkeypatch.delenv("DATABASE_URL")
    else:
        monkeypatch.setenv("DATABASE_URL", value)

    def must_not_open_database(url):
        pytest.fail("A production migration must reject missing/Postgres-invalid configuration before opening any database")

    monkeypatch.setattr(database, "make_engine", must_not_open_database)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        command.upgrade(Config("alembic.ini"), "head")


def test_explicit_production_also_requires_postgres(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_ID", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is missing"):
        database_url_from_env()


def test_local_development_keeps_sqlite_default(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_ID", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url_from_env() == "sqlite:///./food-diary.db"
