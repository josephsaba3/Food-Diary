"""View model for the /health page: averages, chart geometry and the daily log.

Everything is computed here so the page renders fully on the server; app.js only
moves the selected day and opens the day sheet. A day counts as "worn" when it has
a resting heart rate. Averages use worn days only.
"""
import math
from datetime import date, timedelta

PERIODS = (1, 7, 30, 90)
DEFAULT_PERIOD = 1
MAX_DAYS = 93
LOG_PREVIEW = 10


def short(d):
    return f"{d:%a} {d.day} {d:%b}"


def long(d):
    return f"{d:%A} {d.day} {d:%B}"


def range_label(start, end):
    if start == end:
        return f"{end.day} {end:%b %Y}"
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
    days = period if period in PERIODS else DEFAULT_PERIOD
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


# ---- through the day ------------------------------------------------------------

DAY_MINUTES = 1440


def _clock(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _minutes(hhmm):
    try:
        hours, mins = hhmm[:5].split(":")
        return int(hours) * 60 + int(mins)
    except (AttributeError, ValueError):
        return None


def _runs(points):
    """Split (slot minute, ...) points into runs of back-to-back slots so gaps in wear stay gaps."""
    runs, current, last = [], [], None
    for point in points:
        if last is not None and point[0] - last > 15:
            runs.append(current)
            current = []
        current.append(point)
        last = point[0]
    if current:
        runs.append(current)
    return runs


STRESS_BANDS = ((25, "rest"), (50, "low"), (75, "medium"), (100, "high"))  # Garmin's own stress ranges
EVENT_NAMES = {"stress": "Stress", "recovery": "Recovery"}


def stress_band(level):
    return next(name for top, name in STRESS_BANDS if level <= top)


def timeline(day, slots, activities=(), meals=None, events=()):
    """Geometry for the 15-minute heart rate, Body Battery and stress charts of one day (SVG viewBox 0 0 1440 100)."""
    def x(minute):
        return minute + 7.5  # centre of the 15-minute slot

    rows = [(_minutes(s["time"]), s) for s in slots if _minutes(s.get("time")) is not None]
    heart = [(m, s["hr"], s.get("hr_min", s["hr"]), s.get("hr_max", s["hr"])) for m, s in rows if s.get("hr") is not None]
    battery = [(m, s["body_battery"]) for m, s in rows if s.get("body_battery") is not None]

    hr_lo = hr_hi = None
    hr_band = hr_line = ""
    hr_lines = []
    if heart:
        hr_lo = max(0, math.floor((min(h[2] for h in heart) - 5) / 10) * 10)
        hr_hi = math.ceil((max(h[3] for h in heart) + 5) / 10) * 10
        y = lambda v: 100 - pct(v, hr_lo, hr_hi)
        band, line = [], []
        for run in _runs(heart):
            top = [f"{x(m):.1f} {y(hi):.1f}" for m, _, _, hi in run]
            bottom = [f"{x(m):.1f} {y(lo):.1f}" for m, _, lo, _ in reversed(run)]
            if len(run) == 1:  # a lone slot still needs width to be seen
                m, _, lo, hi = run[0]
                top = [f"{m + 2:.1f} {y(hi):.1f}", f"{m + 13:.1f} {y(hi):.1f}"]
                bottom = [f"{m + 13:.1f} {y(lo):.1f}", f"{m + 2:.1f} {y(lo):.1f}"]
            band.append("M" + " L".join(top + bottom) + " Z")
            line.append("M" + " L".join(f"{x(m):.1f} {y(avg):.1f}" for m, avg, _, _ in run))
        hr_band, hr_line = " ".join(band), " ".join(line)
        step = 20 if hr_hi - hr_lo > 60 else 10
        hr_lines = [{"bottom": pct(v, hr_lo, hr_hi), "label": str(v)}
                    for v in range(hr_lo + step, hr_hi, step)]

    bb_line = bb_area = ""
    if battery:
        y = lambda v: 100 - v
        line, area = [], []
        for run in _runs(battery):
            points = [f"{x(m):.1f} {y(v):.1f}" for m, v in run]
            if len(run) == 1:
                m, v = run[0]
                points = [f"{m + 2:.1f} {y(v):.1f}", f"{m + 13:.1f} {y(v):.1f}"]
            line.append("M" + " L".join(points))
            first, last = points[0].split()[0], points[-1].split()[0]
            area.append(f"M{first} 100 L" + " L".join(points) + f" L{last} 100 Z")
        bb_line, bb_area = " ".join(line), " ".join(area)

    spans = []
    for a in activities or ():
        start = _minutes((a.get("start") or "")[11:16])
        if start is None or not (a.get("start") or "").startswith(day.isoformat()):
            continue
        length = max(5, round(a.get("duration_minutes") or 0))
        end = min(DAY_MINUTES, start + length)
        name = a.get("name") or (a.get("type") or "workout").replace("_", " ").capitalize()
        spans.append({"left": start / DAY_MINUTES * 100, "width": max(0.4, (end - start) / DAY_MINUTES * 100),
                      "label": f"{name}, {_clock(start)}–{_clock(end % DAY_MINUTES)}"})

    stress = [(m, s["stress"], s.get("stress_max", s["stress"])) for m, s in rows if s.get("stress") is not None]
    stress_bars = [{"x": m + 1.5, "y": 100 - level, "h": max(level, 1.5), "band": stress_band(level),
                    "label": f"{_clock(m)} stress {level}"} for m, level, _ in stress]
    periods = []
    for e in events or ():
        start, end = _minutes(e.get("start")), _minutes(e.get("end"))
        if e.get("type") not in EVENT_NAMES or start is None or end is None or end <= start:
            continue
        impact = e.get("body_battery_impact")
        label = f"{EVENT_NAMES[e['type']]}, {e['start']}–{e['end']}"
        if isinstance(impact, (int, float)):
            label += f", Body Battery {'+' if impact > 0 else '−' if impact < 0 else ''}{abs(impact)}"
        periods.append({"kind": e["type"], "left": start / DAY_MINUTES * 100,
                        "width": max(0.4, (end - start) / DAY_MINUTES * 100), "label": label})
    stress_minutes = sum(_minutes(e["end"]) - _minutes(e["start"]) for e in events or ()
                         if e.get("type") == "stress" and _minutes(e.get("start")) is not None and _minutes(e.get("end")) is not None)

    marks = []
    for category, meal in (meals or {}).items():
        at = _minutes(meal.get("time") or "")
        if at is not None:
            marks.append({"left": (at + 0.5) / DAY_MINUTES * 100, "time": _clock(at),
                          "label": f"{category.capitalize()} {_clock(at)}"})
    marks.sort(key=lambda m: m["left"])

    hr_values = [h[1] for h in heart]
    peak = max(heart, key=lambda h: h[3]) if heart else None
    summary = {
        "hr": f"{min(h[2] for h in heart)}–{max(h[3] for h in heart)}" if heart else None,
        "hr_peak": f"Peak {peak[3]} bpm at {_clock(peak[0])}" if peak else None,
        "hr_avg": round(sum(hr_values) / len(hr_values)) if hr_values else None,
        "bb": f"{battery[0][1]} → {battery[-1][1]}" if battery else None,
        "bb_range": (lambda hi, lo: f"High {hi[1]} at {_clock(hi[0])} · low {lo[1]} at {_clock(lo[0])}")(
            max(battery, key=lambda b: b[1]), min(battery, key=lambda b: b[1])) if battery else None,
    }
    stress_peak = max(stress, key=lambda st: st[2]) if stress else None
    summary.update({
        "stress": round(sum(st[1] for st in stress) / len(stress)) if stress else None,
        "stress_peak": f"Peak {stress_peak[2]} at {_clock(stress_peak[0])}" if stress_peak else None,
        "stress_periods": (f"{sum(1 for p in periods if p['kind'] == 'stress')} stress "
                           f"{'period' if sum(1 for p in periods if p['kind'] == 'stress') == 1 else 'periods'}, "
                           f"{stress_minutes // 60}h {stress_minutes % 60:02d}m") if stress_minutes else None,
    })
    return {
        "iso": day.isoformat(), "long": long(day), "has_data": bool(slots) or bool(periods),
        "stress_bars": stress_bars, "periods": periods,
        "show_stress": bool(stress_bars or periods),
        "slots": slots, "summary": summary,
        "hr_band": hr_band, "hr_line": hr_line, "hr_lines": hr_lines,
        "bb_line": bb_line, "bb_area": bb_area,
        "spans": spans, "meals": marks,
        "axis": [{"left": h / 24 * 100, "text": f"{h:02d}:00" if h < 24 else "24:00",
                  "align": "start" if h == 0 else "end" if h == 24 else "center"} for h in (0, 6, 12, 18, 24)],
        "hr_lo": hr_lo, "hr_hi": hr_hi,
    }
