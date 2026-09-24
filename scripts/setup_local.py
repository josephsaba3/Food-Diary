"""Create a local-only configuration. Never replace an existing .env."""
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
(root / "private").mkdir(exist_ok=True)
env = root / ".env"
if env.exists():
    print("Existing .env kept.")
else:
    env.write_text("\n".join([
        "APP_ENV=development", "DATABASE_URL=sqlite:///./private/diary.db",
        "BASE_URL=http://localhost:8000", "TIMEZONE=Australia/Sydney",
        "APP_PASSWORD=" + secrets.token_urlsafe(18),
        "SESSION_SECRET=" + secrets.token_urlsafe(48), "",
    ]), encoding="utf-8")
    print("Created .env with a private local password. Open .env to find APP_PASSWORD.")
