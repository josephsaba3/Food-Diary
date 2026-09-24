from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint
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

