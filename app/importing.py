from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .models import DayNote, Meal, Water
from .schemas import MAX_WATER_ML, Category, MealInput


class ImportedMeal(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: Category
    time: str | None = None
    food: str
    symptoms: str = ""
    symptom_status: str = "unrecorded"


class ImportedDay(BaseModel):
    date: date
    meals: list[ImportedMeal] = Field(max_length=6)
    notes: str = Field(default="", max_length=10000)
    water: dict[Category, Annotated[int, Field(ge=0, le=MAX_WATER_ML)]] = Field(default_factory=dict)


class ImportBundle(BaseModel):
    format: str = "food-diary-v1"
    days: list[ImportedDay] = Field(max_length=367)


def import_bundle(session, bundle: ImportBundle):
    if bundle.format != "food-diary-v1":
        raise ValueError("Choose a Food diary JSON export.")
    # Validate the whole bundle before writing any of it.
    validated = []
    for day in bundle.days:
        for meal in day.meals:
            validated.append((day.date, meal.category.value, MealInput(**meal.model_dump(exclude={"category"}), expected_version=0)))
    added = skipped = 0
    for day, category, meal in validated:
        exists = session.scalar(select(Meal.id).where(Meal.day == day, Meal.category == category))
        if exists:
            skipped += 1
        else:
            session.add(Meal(day=day, category=category, **meal.model_dump(exclude={"expected_version"})))
            session.flush()
            added += 1
    notes_added = 0
    for day in bundle.days:
        if day.notes and not session.get(DayNote, day.date):
            session.add(DayNote(day=day.date, text=day.notes))
            session.flush()
            notes_added += 1
    water_added = 0
    for day in bundle.days:  # like meals, a slot that already has water keeps it
        for category, ml in day.water.items():
            if ml and not session.get(Water, (day.date, category.value)):
                session.add(Water(day=day.date, category=category.value, ml=ml))
                session.flush()
                water_added += 1
    session.commit()
    return {"added": added, "skipped": skipped, "notes_added": notes_added, "water_added": water_added}

