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
    # Tile headings and second lines: a period's averages, or a single day's own figures.
    if n == 1 and worn:
        s = worn[0]["summary"]
        hr, stress, bb, st = (s.get(k) or {} for k in ("heart_rate", "stress", "body_battery", "steps"))
        averages.update(
            heading="The day", steps_label="Steps", rhr=str(worn[0]["rhr"]),
            steps_sub=f"Goal {fmt(st['goal'])}" if st.get("goal") else None,
            rhr_sub=f"Range {hr['min']}–{hr['max']} bpm" if hr.get("min") is not None and hr.get("max") is not None else None,
            stress_sub=f"Peak {stress['max']}" if stress.get("max") is not None else None,
            bb_sub=f"Low {bb['lowest']}" if bb.get("lowest") is not None else None)
    else:
        averages.update(
            heading="Averages", steps_label="Steps per day",
            steps_sub=f"Best {averages['steps_best']}" if averages["steps_best"] else None,
            rhr_sub=f"Range {averages['rhr_range']}" if averages["rhr_range"] else None,
            stress_sub=f"Highest day {averages['stress_peak']}" if averages["stress_peak"] else None,
            bb_sub=f"Lowest {averages['bb_low']}" if averages["bb_low"] else None)

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


# ---- through the day (a single day in 15-minute slots) ------------------------

DAY_MINUTES = 1440
JOIN_BODY_BATTERY = 180  # Body Battery is a level, so readings up to 3 hours apart are joined by the line
STRESS_BANDS = ((25, "rest"), (50, "low"), (75, "medium"), (100, "high"))  # Garmin's own stress ranges
EVENT_NAMES = {"stress": "Stress", "recovery": "Recovery"}


def stress_band(level):
    return next(name for top, name in STRESS_BANDS if level <= top)


def _clock(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _minutes(hhmm):
    try:
        hours, mins = hhmm[:5].split(":")
        return int(hours) * 60 + int(mins)
    except (AttributeError, ValueError):
        return None


def _runs(points, join=15):
    """Split (slot minute, ...) points into runs, breaking where readings are more than `join` minutes apart."""
    runs, current, last = [], [], None
    for point in points:
        if last is not None and point[0] - last > join:
            runs.append(current)
            current = []
        current.append(point)
        last = point[0]
    if current:
        runs.append(current)
    return runs


def _x(minute):
    return minute + 7.5  # centre of the 15-minute slot, in a 0-1440 viewBox


def _bars(points, top):
    """SVG bars for (minute, value) points on a 0-top scale."""
    return [{"x": m + 1.5, "y": 100 - pct(v, 0, top), "h": max(pct(v, 0, top), 1.5), "value": v, "minute": m}
            for m, v in points]


def _nice_top(peak, step):
    return max(step * 2, math.ceil(peak * 1.05 / step) * step)


def timeline(day, slots, activities=(), meals=None, events=(), summary=None):
    """The single-day view: steps, heart rate, stress and Body Battery in 15-minute slots (SVG viewBox 0 0 1440 100).

    summary is the day's Garmin summary, used for totals and when a metric isn't available slot by slot.
    """
    summary = summary or {}
    rows = [(_minutes(s["time"]), s) for s in slots if _minutes(s.get("time")) is not None]
    pick = lambda key: [(m, s[key]) for m, s in rows if s.get(key) is not None]
    steps, battery, stress = pick("steps"), pick("body_battery"), pick("stress")
    heart = [(m, s["hr"], s.get("hr_min", s["hr"]), s.get("hr_max", s["hr"])) for m, s in rows if s.get("hr") is not None]

    # Steps ------------------------------------------------------------------
    day_steps = (summary.get("steps") or {}).get("total")
    steps_top = _nice_top(max(v for _, v in steps), 250) if steps else 500
    busiest = max(steps, key=lambda p: p[1]) if steps else None
    steps_chart = {
        "bars": _bars(steps, steps_top),
        "lines": [{"bottom": 50.0, "label": f"{steps_top // 2:,}"}, {"bottom": 100.0, "label": f"{steps_top:,}"}],
        "value": f"{day_steps:,}" if day_steps is not None else (f"{sum(v for _, v in steps):,}" if steps else None),
        "sub": f"Busiest 15 minutes {busiest[1]:,} at {_clock(busiest[0])}" if busiest and busiest[1] else None,
    }

    # Heart rate ---------------------------------------------------------------
    resting = (summary.get("heart_rate") or {}).get("resting")
    hr_chart = {"band": "", "line": "", "lines": [], "resting": None, "value": None, "sub": None}
    if heart:
        lows = [h[2] for h in heart] + ([resting] if resting else [])
        lo = max(0, math.floor((min(lows) - 5) / 10) * 10)
        hi = math.ceil((max(h[3] for h in heart) + 5) / 10) * 10
        y = lambda v: 100 - pct(v, lo, hi)
        band, line = [], []
        for run in _runs(heart):
            if len(run) == 1:  # a lone slot still needs width to be seen
                m, _, low, high = run[0]
                top = [f"{m + 2:.1f} {y(high):.1f}", f"{m + 13:.1f} {y(high):.1f}"]
                bottom = [f"{m + 13:.1f} {y(low):.1f}", f"{m + 2:.1f} {y(low):.1f}"]
            else:
                top = [f"{_x(m):.1f} {y(high):.1f}" for m, _, _, high in run]
                bottom = [f"{_x(m):.1f} {y(low):.1f}" for m, _, low, _ in reversed(run)]
            band.append("M" + " L".join(top + bottom) + " Z")
            line.append("M" + " L".join(f"{_x(m):.1f} {y(avg):.1f}" for m, avg, _, _ in run))
        step = 20 if hi - lo > 60 else 10
        peak = max(heart, key=lambda h: h[3])
        average = round(sum(h[1] for h in heart) / len(heart))
        hr_chart.update(band=" ".join(band), line=" ".join(line),
                        lines=[{"bottom": pct(v, lo, hi), "label": str(v)} for v in range(lo + step, hi, step)],
                        resting={"bottom": pct(resting, lo, hi), "label": f"resting {resting}"} if resting else None,
                        value=f"{min(h[2] for h in heart)}–{max(h[3] for h in heart)}",
                        sub=f"Peak {peak[3]} bpm at {_clock(peak[0])} · average {average}")

    # Stress -----------------------------------------------------------------
    periods = []
    stress_minutes = 0
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
        if e["type"] == "stress":
            stress_minutes += end - start
    stress_count = sum(1 for p in periods if p["kind"] == "stress")
    period_text = (f"{stress_count} stress {'period' if stress_count == 1 else 'periods'}, "
                   f"{stress_minutes // 60}h {stress_minutes % 60:02d}m") if stress_count else None
    stress_peak = max(((m, s.get("stress_max", s["stress"])) for m, s in rows if s.get("stress") is not None),
                      key=lambda p: p[1], default=None)
    day_stress = (summary.get("stress") or {}).get("average")
    stress_bars = _bars(stress, 100)
    for bar in stress_bars:
        bar["band"] = stress_band(bar["value"])
    stress_chart = {
        "bars": stress_bars, "periods": periods,
        # Garmin's own daily average, so it matches the figure above; the slots' average only if it's missing.
        "value": day_stress if day_stress is not None else (round(sum(v for _, v in stress) / len(stress)) if stress else None),
        "sub": " · ".join(t for t in (f"Peak {stress_peak[1]} at {_clock(stress_peak[0])}" if stress_peak else None,
                                      period_text) if t) or None,
    }

    # Body Battery -----------------------------------------------------------
    bb_chart = {"line": "", "area": "", "dots": [], "value": None, "sub": None}
    if battery:
        y = lambda v: 100 - v
        line, area = [], []
        for run in _runs(battery, JOIN_BODY_BATTERY):
            if len(run) == 1:
                m, v = run[0]
                bb_chart["dots"].append(f"M{_x(m):.1f} {y(v):.1f} l0.01 0")
                continue
            points = [f"{_x(m):.1f} {y(v):.1f}" for m, v in run]
            line.append("M" + " L".join(points))
            area.append(f"M{_x(run[0][0]):.1f} 100 L" + " L".join(points) + f" L{_x(run[-1][0]):.1f} 100 Z")
        high, low = max(battery, key=lambda b: b[1]), min(battery, key=lambda b: b[1])
        bb_chart.update(line=" ".join(line), area=" ".join(area), dots=" ".join(bb_chart["dots"]),
                        value=f"{battery[0][1]} → {battery[-1][1]}",
                        sub=f"High {high[1]} at {_clock(high[0])} · low {low[1]} at {_clock(low[0])}")

    # Workouts and meals, drawn on every chart -----------------------------------
    spans = []
    for a in activities or ():
        start = _minutes((a.get("start") or "")[11:16])
        if start is None or not (a.get("start") or "").startswith(day.isoformat()):
            continue
        end = min(DAY_MINUTES, start + max(5, round(a.get("duration_minutes") or 0)))
        name = a.get("name") or (a.get("type") or "workout").replace("_", " ").capitalize()
        spans.append({"left": start / DAY_MINUTES * 100, "width": max(0.4, (end - start) / DAY_MINUTES * 100),
                      "label": f"{name}, {_clock(start)}–{_clock(end % DAY_MINUTES)}"})
    marks = []
    for category, meal in (meals or {}).items():
        at = _minutes(meal.get("time") or "")
        if at is not None:
            marks.append({"left": (at + 0.5) / DAY_MINUTES * 100, "label": f"{category.capitalize()} {_clock(at)}"})
    marks.sort(key=lambda m: m["left"])

    return {
        "iso": day.isoformat(), "long": long(day), "has_data": bool(slots) or bool(periods),
        "slots": slots, "steps": steps_chart, "hr": hr_chart, "stress": stress_chart, "bb": bb_chart,
        "spans": spans, "meals": marks,
        "columns": {"steps": bool(steps), "stress": bool(stress)},
        "axis": [{"left": h / 24 * 100, "text": f"{h:02d}:00", "align": "start" if h == 0 else "end" if h == 24 else "center"}
                 for h in (0, 6, 12, 18, 24)],
    }
