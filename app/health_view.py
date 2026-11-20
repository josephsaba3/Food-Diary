"""View model for the /health page: averages, chart geometry and the daily log.

Everything is computed here so the page renders fully on the server; app.js only
moves the selected day and opens the day sheet. A day counts as "worn" when it has
a resting heart rate. Averages use worn days only.
"""
import math
from datetime import date, timedelta

PERIODS = (7, 30, 90)
MAX_DAYS = 93
LOG_PREVIEW = 10


def short(d):
    return f"{d:%a} {d.day} {d:%b}"


def long(d):
    return f"{d:%A} {d.day} {d:%B}"


def range_label(start, end):
    if start.year != end.year:
        return f"{start.day} {start:%b %Y} – {end.day} {end:%b %Y}"
    if start.month == end.month:
        return f"{start.day} – {end.day} {end:%b %Y}"
    return f"{start.day} {start:%b} – {end.day} {end:%b %Y}"


def fmt(n):
    return f"{round(n):,}" if n is not None else "–"


def pct(v, lo, hi):
    return max(0.0, min(100.0, (v - lo) / (hi - lo) * 100)) if hi > lo else 0.0


def workout_label(activities):
    if not activities:
        return None
    first = activities[0]
    name = (first.get("type") or "workout").replace("_", " ").capitalize()
    if first.get("duration_minutes"):
        name += f" · {round(first['duration_minutes'])} min"
    return name + (f" +{len(activities) - 1}" if len(activities) > 1 else "")


def resolve_period(today, period=None, start=None, end=None):
    """Return (start, end, period_key). Custom ranges are clamped to MAX_DAYS and to today."""
    if start and end:
        end = min(end, today)
        start = min(start, end)
        if (end - start).days + 1 > MAX_DAYS:
            start = end - timedelta(days=MAX_DAYS - 1)
        key = str((end - start).days + 1) if end == today and (end - start).days + 1 in PERIODS else "custom"
        return start, end, key
    days = period if period in PERIODS else 30
    return today - timedelta(days=days - 1), today, str(days)


def axis_labels(days, n):
    if n == 1:
        return [{"left": 50.0, "text": f"{days[0]['date'].day} {days[0]['date']:%b}", "align": "center"}]
    picks = sorted({0, round((n - 1) / 4), round((n - 1) / 2), round(3 * (n - 1) / 4), n - 1})
    labels = []
    for i in picks:
        d = days[i]["date"]
        align = "start" if i == 0 else "end" if i == n - 1 else "center"
        labels.append({"left": 0 if i == 0 else 100 if i == n - 1 else (i + 0.5) / n * 100, "text": f"{d.day} {d:%b}", "align": align})
    return labels


def nice_steps_top(peak):
    top = max(10000, math.ceil(peak * 1.03 / 2500) * 2500)
    step = 5000 if top <= 25000 else 10000
    return top, [v for v in range(step, top + 1, step)]


def build(health_days, start, end, today):
    """health_days: list from garmin.health_range (one per date, oldest first)."""
    days = []
    for raw in health_days:
        s = raw["summary"]
        d = date.fromisoformat(raw["date"])
        acts = raw.get("activities") or []
        days.append({
            "date": d, "iso": raw["date"], "short": short(d), "long": long(d),
            "steps": (s.get("steps") or {}).get("total"),
            "rhr": (s.get("heart_rate") or {}).get("resting"),
            "stress": (s.get("stress") or {}).get("average"),
            "bb_high": (s.get("body_battery") or {}).get("highest"),
            "bb_low": (s.get("body_battery") or {}).get("lowest"),
            "workout": workout_label(acts), "activities": acts, "summary": s,
            "synced_at": raw.get("synced_at"),
        })
    for x in days:
        x["worn"] = x["rhr"] is not None
    n = len(days)
    worn = [x for x in days if x["worn"]]

    def values(key):
        return [x for x in worn if x[key] is not None]

    def mean(key):
        vals = [x[key] for x in values(key)]
        return sum(vals) / len(vals) if vals else None

    def best(key, highest=True):
        vals = values(key)
        return (max if highest else min)(vals, key=lambda x: x[key]) if vals else None

    top_steps, best_stress = best("steps"), best("stress")
    rhrs = [x["rhr"] for x in worn]
    lows = [x["bb_low"] for x in values("bb_low")]
    averages = {
        "steps": fmt(mean("steps")) if mean("steps") is not None else None,
        "steps_best": f"{fmt(top_steps['steps'])} · {top_steps['date'].day} {top_steps['date']:%b}" if top_steps else None,
        "rhr": f"{mean('rhr'):.1f}" if worn else None,
        "rhr_range": f"{min(rhrs)}–{max(rhrs)} bpm" if rhrs else None,
        "stress": fmt(mean("stress")) if mean("stress") is not None else None,
        "stress_peak": f"{best_stress['stress']} · {best_stress['date'].day} {best_stress['date']:%b}" if best_stress else None,
        "bb": fmt(mean("bb_high")) if mean("bb_high") is not None else None,
        "bb_low": f"{min(lows)} at its low" if lows else None,
    }

    # Selection defaults to the most recent worn day.
    selected = max((i for i, x in enumerate(days) if x["worn"]), default=n - 1)

    peak = max([x["steps"] or 0 for x in days] + [0])
    steps_top, steps_lines = nice_steps_top(peak)
    rhr_lo = rhr_hi = None
    if rhrs:
        rhr_lo = math.floor((min(rhrs) - 4) / 5) * 5
        rhr_hi = math.ceil((max(rhrs) + 4) / 5) * 5
    rhr_lines = [v for v in range((rhr_lo or 0) + 1, (rhr_hi or 0)) if v % 10 == 0] if rhrs else []

    columns = []
    path, pen = [], False
    for i, x in enumerate(days):
        col = {"i": i, "aria": x["short"] + (", no readings" if not x["worn"] else "")}
        col["steps_h"] = pct(x["steps"], 0, steps_top) if x["steps"] is not None and x["worn"] else None
        col["stress_h"] = pct(x["stress"], 0, 100) if x["stress"] is not None and x["worn"] else None
        if x["worn"] and x["bb_high"] is not None:
            lo = pct(x["bb_low"] if x["bb_low"] is not None else x["bb_high"], 0, 100)
            col["bb"] = (lo, max(2.0, pct(x["bb_high"], 0, 100) - lo))
        else:
            col["bb"] = None
        if x["worn"]:
            y = pct(x["rhr"], rhr_lo, rhr_hi)
            col["rhr_y"] = y
            path.append(f"{'L' if pen else 'M'}{(i + 0.5) / n * 300:.1f} {100 - y:.1f}")
            pen = True
        else:
            col["rhr_y"] = None
            pen = False
        col["workout"] = bool(x["workout"]) and x["worn"]
        columns.append(col)

    charts = {
        "steps_lines": [{"bottom": pct(v, 0, steps_top), "label": f"{v // 1000}k"} for v in steps_lines],
        "steps_avg": pct(mean("steps"), 0, steps_top) if mean("steps") is not None else None,
        "rhr_lines": [{"bottom": pct(v, rhr_lo, rhr_hi), "label": str(v)} for v in rhr_lines],
        "rhr_path": " ".join(path),
        "axis": axis_labels(days, n) if n else [],
        "dense": n > 45,
    }

    # Daily log: newest first, runs of unworn days collapsed into one row.
    rows, i = [], n - 1
    while i >= 0:
        if not days[i]["worn"]:
            j = i
            while j - 1 >= 0 and not days[j - 1]["worn"]:
                j -= 1
            a, b, count = days[j]["date"], days[i]["date"], i - j + 1
            if count == 1:
                title = short(a)
            elif a.month == b.month:
                title = f"{a.day}–{b.day} {b:%b}"
            else:
                title = f"{a.day} {a:%b} – {b.day} {b:%b}"
            rows.append({"gap": True, "title": title, "sub": "No watch readings" + (f" · {count} days" if count > 1 else "")})
            i = j - 1
        else:
            x = days[i]
            sub = [f"{fmt(x['steps'])} steps" if x["steps"] is not None else None,
                   f"{x['rhr']} bpm", f"stress {x['stress']}" if x["stress"] is not None else None]
            rows.append({"gap": False, "i": i, "iso": x["iso"], "title": x["short"], "day": x["date"].day,
                         "steps": fmt(x["steps"]), "rhr": f"{x['rhr']} bpm",
                         "stress": str(x["stress"]) if x["stress"] is not None else "–",
                         "bb": f"{x['bb_low']}–{x['bb_high']}" if x["bb_high"] is not None and x["bb_low"] is not None else "–",
                         "workout": x["workout"], "sub": " · ".join(v for v in sub if v)})
            i -= 1

    # Everything the browser needs to move the selection and fill the day sheet.
    client = [{"iso": x["iso"], "short": x["short"], "long": x["long"], "worn": x["worn"],
               "steps": x["steps"], "rhr": x["rhr"], "stress": x["stress"], "bb_high": x["bb_high"], "bb_low": x["bb_low"],
               "workout": x["workout"], "summary": x["summary"], "activities": x["activities"],
               "synced_at": x["synced_at"]} for x in days]

    return {"start": start, "end": end, "label": range_label(start, end), "days": days, "n": n,
            "worn_count": len(worn), "averages": averages, "selected": selected, "columns": columns,
            "charts": charts, "rows": rows, "log_preview": LOG_PREVIEW, "client": client}
