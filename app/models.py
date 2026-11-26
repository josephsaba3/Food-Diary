from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Meal(Base):
    __tablename__ = "meals"
    __table_args__ = (UniqueConstraint("day", "category", name="uq_meal_day_category"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(32))
    time: Mapped[str | None] = mapped_column(String(5))
    food: Mapped[str] = mapped_column(Text)
    symptoms: Mapped[str] = mapped_column(Text, default="")
    symptom_status: Mapped[str] = mapped_column(String(16), default="unrecorded")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DayNote(Base):
    __tablename__ = "day_notes"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthRecord(Base):
    """Short-lived OAuth state and hashed credentials; shared across restarts."""
    __tablename__ = "auth_records"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    payload: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[int] = mapped_column(index=True)



class GarminAccount(Base):
    """The single linked Garmin account: Personal API key and current bearer token, both Fernet-encrypted."""
    __tablename__ = "garmin_account"
    id: Mapped[int] = mapped_column(primary_key=True)
    api_key: Mapped[str] = mapped_column(Text)
    access_token: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[int | None] = mapped_column(BigInteger)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")
    # Optional Garmin account sign-in for data the Personal API doesn't serve (steps and stress every 15 minutes):
    # Garmin's sign-in tokens, Fernet-encrypted. The password itself is never stored.
    account_tokens: Mapped[str | None] = mapped_column(Text)
    account_linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account_error: Mapped[str] = mapped_column(Text, default="")


class HealthDay(Base):
    """One summarised Garmin day (steps, sleep, heart rate, Body Battery, stress, HRV...) as JSON."""
    __tablename__ = "health_days"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    summary: Mapped[str] = mapped_column(Text)
    # Trimmed Garmin responses, kept so fields the summary doesn't map are still readable.
    raw: Mapped[str] = mapped_column(Text, default="{}")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HealthActivity(Base):
    __tablename__ = "health_activities"
    activity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    day: Mapped[date] = mapped_column(Date, index=True)
    summary: Mapped[str] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HealthTimeline(Base):
    """One Garmin day in 15-minute slots (heart rate, Body Battery, stress when Garmin shares it) as JSON,
    plus the timed Body Battery events (stress, recovery...), keyed by the watch's local day."""
    __tablename__ = "health_timelines"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    slots: Mapped[str] = mapped_column(Text)
    events: Mapped[str] = mapped_column(Text, default="[]")
    version: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PushSubscription(Base):
    """A phone or browser that asked for alerts: its Web Push endpoint and the keys to encrypt messages for it."""
    __tablename__ = "push_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    device: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")


class StressAlert(Base):
    """A high-stress stretch an alert was sent for, so each stretch is only announced once."""
    __tablename__ = "stress_alerts"
    __table_args__ = (UniqueConstraint("day", "start_minute", name="uq_stress_alert_day_start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    start_minute: Mapped[int] = mapped_column(Integer)
    minutes: Mapped[int] = mapped_column(Integer)
    average: Mapped[int | None] = mapped_column(Integer)
    peak: Mapped[int | None] = mapped_column(Integer)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
