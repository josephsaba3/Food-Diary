"""View models for the Today, Week and Report pages.

Food stays free text and symptoms stay free text: the chips on the pages are only a way of showing and
typing them. food_items() splits a description into chips; symptom text written by the Log sheet's
shortcuts ("Bloating, cramps · severity 2/5 (mild) · Bristol type 5 · notes") reads as plain text anywhere.
"""
import re
from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import select

from .models import DayNote, Meal, Water
from .schemas import CATEGORIES, IMPORT_FROM

SPLIT = re.compile(r"\s*(?:\n|;|,|\s\+\s)\s*")
SHORT_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
SHORT_SLOTS = {"breakfast": "Brk", "mid_morning_snack": "AM", "lunch": "Lun", "mid_afternoon_snack": "PM",
               "dinner": "Din", "evening_snack": "Eve"}


def food_items(text):
    """A food description as chips: split on new lines, semicolons, commas and ' + '."""
    return [part for part in SPLIT.split(text or "") if part]


def short_food(text, limit=42):
    joined = " + ".join(food_items(text)) or (text or "")
    return joined if len(joined) <= limit else joined[:limit - 1].rstrip(" +,") + "…"


def day_label(d, today):
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return f"{d:%A}"


def long_date(d):
    return f"{d:%A} {d.day} {d:%B}"


def _meals_between(session, start, end):
    return session.scalars(select(Meal).where(Meal.day >= start, Meal.day <= end)).all()


def symptoms_summary(meals):
    """The Today tile: whether the day's symptoms are recorded. Blank never reads as 'none'."""
    if not meals:
        return {"value": "Nothing logged", "tone": "muted"}
    statuses = [m["symptom_status"] for m in meals]
    reported = statuses.count("reported")
    unrecorded = statuses.count("unrecorded")
    if reported:
        return {"value": f"{reported} reported", "tone": "amber"}
    if unrecorded == len(statuses):
        return {"value": "Not yet recorded", "tone": "amber"}
    if unrecorded:
        return {"value": f"{unrecorded} to record", "tone": "amber"}
    return {"value": "None so far", "tone": "green"}


def suggestions(session, day, today):
    """For the Log sheet, per meal slot: foods often logged there and the most recent one to repeat."""
    since = day - timedelta(days=120)
    rows = session.scalars(select(Meal).where(Meal.day >= since, Meal.day <= day).order_by(Meal.day.desc())).all()
    out = {}
    for key in CATEGORIES:
        mine = [m for m in rows if m.category == key]
        counts, latest = Counter(), {}
        for m in mine:
            k = " ".join(m.food.lower().split())
            counts[k] += 1
            latest.setdefault(k, m.food)
        often = [{"label": short_food(latest[k]), "food": latest[k]} for k, n in counts.most_common(8) if n >= 2][:5]
        previous = next((m for m in mine if m.day < day), None)
        repeat = None
        if previous:
            when = (f"{previous.day:%A}'s" if (day - previous.day).days < 7 else f"{previous.day.day} {previous.day:%b}'s")
            if previous.day == today - timedelta(days=1):
                when = "yesterday's"
            repeat = {"label": f"Repeat {when} {CATEGORIES[key].lower()}", "food": previous.food}
        out[key] = {"often": often, "repeat": repeat}
    return out


def copy_choices(session, day):
    """'Copy from yesterday': yesterday's meals, each into the same slot, and dinner as leftovers for lunch."""
    yesterday = day - timedelta(days=1)
    meals = {m.category: m for m in session.scalars(select(Meal).where(Meal.day == yesterday))}
    choices = [{"target": key, "label": f"{CATEGORIES[key]}", "food": meals[key].food}
               for key in CATEGORIES if key in meals]
    for target, source in IMPORT_FROM.items():
        if target != source and source in meals:
            choices.append({"target": target, "label": f"{CATEGORIES[source]} as today’s {CATEGORIES[target].lower()}",
                            "food": meals[source].food})
    return choices


def watch_note(meal, slots, resting):
    """A measured heart-rate note for the 90 minutes from a meal's time, only when it rose well above resting."""
    if not meal.get("time") or not slots:
        return None
    start = int(meal["time"][:2]) * 60 + int(meal["time"][3:5])
    window = [s for s in slots if s.get("hr_max") is not None and start - 15 <= _minutes(s["time"]) <= start + 90]
    if not window:
        return None
    peak = max(s["hr_max"] for s in window)
    if resting and peak < resting + 20:
        return None
    return f"Watch: heart rate peaked at {peak} bpm around this time"


def _minutes(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def water_label(ml):
    """250 ml, 1 L, 1.25 L."""
    return f"{ml} ml" if ml < 1000 else f"{ml / 1000:g} L"


def today_view(session, day, today, now, meals, health_summary, slots, water=None):
    """Everything the Today page shows for one day."""
    monday = day - timedelta(days=day.weekday())
    week = _meals_between(session, monday, monday + timedelta(days=6))
    strip = []
    for i in range(7):
        d = monday + timedelta(days=i)
        mine = [m for m in week if m.day == d]
        strip.append({"date": d, "iso": d.isoformat(), "dow": SHORT_DAYS[i][0], "num": d.day,
                      "label": long_date(d) + (f", {len(mine)} logged" if mine else ""),
                      "selected": d == day, "future": d > today,
                      "dot": "symptom" if any(m.symptom_status == "reported" for m in mine) else "logged" if mine else None})
    resting = ((health_summary or {}).get("heart_rate") or {}).get("resting")
    items, last_logged = [], -1
    for key, label in CATEGORIES.items():
        meal = meals.get(key)
        item = {"key": key, "label": label, "meal": meal, "water": (water or {}).get(key, 0)}
        if meal:
            item.update(chips=food_items(meal["food"]), watch=watch_note(meal, slots, resting))
            last_logged = len(items)
        items.append(item)
    now_at = None
    if day == today:
        now_at = last_logged + 1  # the "Now" line goes after the last meal logged so far
    logged = [m for m in meals.values()]
    steps = ((health_summary or {}).get("steps") or {}).get("total")
    return {
        "strip": strip, "week_start": monday, "timeline": items, "now_at": now_at, "now": now,
        "count": len(logged), "symptoms": symptoms_summary(logged),
        "steps": f"{steps:,}" if steps is not None else None,
        "water_total": sum((water or {}).values()),
    }


def week_view(session, end, today):
    """Seven days ending on `end`: what was logged in each slot and whether symptoms were recorded."""
    start = end - timedelta(days=6)
    meals = _meals_between(session, start, end)
    notes = {n.day for n in session.scalars(select(DayNote).where(DayNote.day >= start, DayNote.day <= end)) if n.text.strip()}
    rows = []
    for i in range(7):
        d = start + timedelta(days=i)
        mine = {m.category: m for m in meals if m.day == d}
        cells = []
        for key, label in CATEGORIES.items():
            m = mine.get(key)
            state = "empty" if not m else "symptom" if m.symptom_status == "reported" else "logged"
            cells.append({"state": state, "label": f"{label}: " + ("not logged" if not m else short_food(m.food, 60)
                          + (", with symptoms" if state == "symptom" else ""))})
        rows.append({"date": d, "iso": d.isoformat(), "name": "Today" if d == today else SHORT_DAYS[d.weekday()],
                     "short": f"{d.day} {d:%b}", "cells": cells, "note": d in notes, "future": d > today})
    recorded = sum(1 for m in meals if m.symptom_status != "unrecorded")
    first_gap = min((m.day for m in meals if m.symptom_status == "unrecorded"), default=None)
    foods = Counter()
    names = {}
    for m in meals:
        for item in food_items(m.food):
            k = item.lower()
            foods[k] += 1
            names.setdefault(k, item[:1].upper() + item[1:])
    top = foods.most_common(6)
    peak = top[0][1] if top else 1
    return {
        "start": start, "end": end, "rows": rows, "slots": [SHORT_SLOTS[k] for k in CATEGORIES],
        "label": f"{start.day} {start:%B} – {end.day} {end:%B}" if start.month != end.month else f"{start.day} – {end.day} {end:%B}",
        "title": "This week" if end == today else "Week",
        "meal_count": len(meals), "days_logged": len({m.day for m in meals}), "recorded": recorded,
        "gap_day": first_gap, "unrecorded": len(meals) - recorded,
        "foods": [{"name": names[k], "count": n, "width": round(n / peak * 100)} for k, n in top if n >= 2][:5],
        "prev": start - timedelta(days=1), "next": min(end + timedelta(days=7), today) if end < today else None,
    }


REPORT_PARTS = (("meals", "Meals and times", "Grouped by day, in the order eaten", True),
                ("symptoms", "Symptoms", "Beside each meal, as written", True),
                ("notes", "Day notes", "Off by default: may hold private details", False),
                ("watch", "Watch summary", "Steps, resting heart rate, stress", False))


def report_view(session, start, end, include, health_days):
    """The dietitian report: meals by day in time order, symptoms as written, optional notes and watch figures."""
    meals = _meals_between(session, start, end)
    notes = {n.day: n.text for n in session.scalars(select(DayNote).where(DayNote.day >= start, DayNote.day <= end)) if n.text.strip()}
    health = {h["date"]: h.get("summary") or {} for h in health_days}
    water = {}
    for w in session.scalars(select(Water).where(Water.day >= start, Water.day <= end, Water.ml > 0)):
        water.setdefault(w.day, {})[w.category] = w.ml
    order = list(CATEGORIES)
    days = []
    for i in range((end - start).days + 1):
        d = start + timedelta(days=i)
        mine = sorted((m for m in meals if m.day == d), key=lambda m: (m.time or "99:99", order.index(m.category)))
        s = health.get(d.isoformat(), {})
        watch = None
        if include.get("watch") and s:
            parts = []
            if (s.get("steps") or {}).get("total") is not None:
                parts.append(f"{s['steps']['total']:,} steps")
            if (s.get("heart_rate") or {}).get("resting") is not None:
                parts.append(f"resting heart rate {s['heart_rate']['resting']} bpm")
            if (s.get("stress") or {}).get("average") is not None:
                parts.append(f"stress {s['stress']['average']} average")
            watch = ", ".join(parts) or None
        drunk = water.get(d, {})
        water_text = (f"{water_label(sum(drunk.values()))} (" + ", ".join(
            f"{CATEGORIES[k].lower()} {water_label(drunk[k])}" for k in order if k in drunk) + ")") if drunk else None
        if not mine and not drunk and not (include.get("notes") and d in notes) and not watch:
            continue
        days.append({"title": long_date(d), "meals": [{
            "time": m.time or "–", "slot": CATEGORIES[m.category], "food": m.food,
            "symptoms": m.symptoms if m.symptom_status == "reported" else "None" if m.symptom_status == "none" else "Not recorded",
            "status": m.symptom_status} for m in mine],
            "water": water_text, "note": notes.get(d) if include.get("notes") else None, "watch": watch})
    unrecorded = sum(1 for m in meals if m.symptom_status == "unrecorded")
    return {"start": start, "end": end, "days": days, "meal_count": len(meals), "unrecorded": unrecorded,
            "span": (end - start).days + 1,
            "label": f"{start.day} {start:%b} – {end.day} {end:%b %Y}" if start != end else f"{end.day} {end:%b %Y}",
            "parts": [{"key": k, "label": label, "sub": sub, "on": bool(include.get(k))} for k, label, sub, _ in REPORT_PARTS]}
