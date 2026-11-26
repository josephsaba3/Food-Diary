from datetime import date, timedelta

from sqlalchemy import case, delete, select, update
from sqlalchemy.exc import IntegrityError

from .models import DayNote, Meal, Water, utcnow
from .schemas import CATEGORIES, IMPORT_FROM, MAX_WATER_ML, MealInput, NoteInput


class ConflictError(ValueError):
    pass


def meal_dict(meal):
    return {"date": meal.day.isoformat(), "category": meal.category, "time": meal.time,
            "food": meal.food, "symptoms": meal.symptoms, "symptom_status": meal.symptom_status,
            "version": meal.version, "updated_at": meal.updated_at.isoformat()}


def get_day(session, day: date):
    rows = session.scalars(select(Meal).where(Meal.day == day)).all()
    meals = {row.category: meal_dict(row) for row in rows}
    yesterday = day - timedelta(days=1)
    previous = {m.category: m for m in session.scalars(select(Meal).where(Meal.day == yesterday))}
    suggestions = {}
    for target, source in IMPORT_FROM.items():
        if source in previous:
            suggestions[target] = {"date": yesterday.isoformat(), "category": source,
                                   "label": f"Import yesterday’s {source}", "food": previous[source].food}
    note = session.get(DayNote, day)
    water = day_water(session, day)
    return {"date": day.isoformat(), "categories": CATEGORIES, "meals": meals, "suggestions": suggestions,
            "note": {"text": note.text if note else "", "version": note.version if note else 0},
            "water": water, "water_total_ml": sum(water.values())}


def day_water(session, day):
    """Millilitres of water per meal slot for a day, leaving out slots with none."""
    rows = session.scalars(select(Water).where(Water.day == day)).all()
    return {row.category: row.ml for row in rows if row.ml > 0}


def add_water(session, day, category, ml):
    """Add (or with a negative amount, take away) water for one slot. Adds commute, so two quick taps in different
    tabs both count; the slot stays between 0 and MAX_WATER_ML. Returns the slot's and the day's totals."""
    for attempt in range(2):
        total = Water.ml + ml
        result = session.execute(update(Water).where(Water.day == day, Water.category == category).values(
            ml=case((total < 0, 0), (total > MAX_WATER_ML, MAX_WATER_ML), else_=total), updated_at=utcnow()))
        if result.rowcount == 1:
            session.commit()
            break
        try:
            session.add(Water(day=day, category=category, ml=min(max(ml, 0), MAX_WATER_ML), updated_at=utcnow()))
            session.commit()
            break
        except IntegrityError:  # added at the same moment elsewhere: add to that row instead
            session.rollback()
    water = day_water(session, day)
    return {"date": day.isoformat(), "category": category, "ml": water.get(category, 0), "total_ml": sum(water.values())}


def save_meal(session, day, category, data: MealInput):
    values = data.model_dump(exclude={"expected_version"})
    values["updated_at"] = utcnow()
    try:
        if data.expected_version == 0:
            session.add(Meal(day=day, category=category, version=1, **values))
            session.flush()
        else:
            result = session.execute(update(Meal).where(Meal.day == day, Meal.category == category,
                Meal.version == data.expected_version).values(**values, version=data.expected_version + 1))
            if result.rowcount != 1:
                raise ConflictError("This meal changed in another tab or in Claude. Reload it before saving.")
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("This meal was added elsewhere. Reload it before saving.") from exc
    meal = session.scalar(select(Meal).where(Meal.day == day, Meal.category == category))
    return meal_dict(meal)


def delete_meal(session, day, category, version):
    result = session.execute(delete(Meal).where(Meal.day == day, Meal.category == category, Meal.version == version))
    if result.rowcount != 1:
        session.rollback()
        raise ConflictError("This meal changed or was already removed. Reload the diary.")
    session.commit()


def save_note(session, day, data: NoteInput):
    try:
        if data.expected_version == 0:
            session.add(DayNote(day=day, text=data.text, version=1))
            session.flush()
        else:
            result = session.execute(update(DayNote).where(DayNote.day == day,
                DayNote.version == data.expected_version).values(text=data.text, version=data.expected_version + 1, updated_at=utcnow()))
            if result.rowcount != 1:
                raise ConflictError("These notes changed elsewhere. Reload them before saving.")
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("Notes were added elsewhere. Reload them before saving.") from exc
    return {"text": data.text, "version": data.expected_version + 1}


def export_days(session, start, end):
    if end < start or (end - start).days > 366:
        raise ValueError("Choose a date range of up to 367 days, with the end on or after the start.")
    return [get_day(session, start + timedelta(days=i)) for i in range((end - start).days + 1)]
