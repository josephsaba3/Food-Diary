"""The redesigned pages: Today, the Log sheet's data, Week and Report."""
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import pages
from tests.test_app import app, client, meal  # noqa: F401  (fixtures)


def today():
    return datetime.now(ZoneInfo("Australia/Sydney")).date()


def put(client, day, category, **values):
    response = client.put(f"/api/days/{day}/meals/{category}", json=meal(**values))
    assert response.status_code == 200, response.text
    return response.json()


def sheet_data(page):
    return json.loads(re.search(r'<script type="application/json" id="today-data">(.*?)</script>', page, re.S).group(1))


def test_food_items_and_symptom_summary():
    assert pages.food_items("Rice Bubbles, LF milk\nBanana; tea + honey") == ["Rice Bubbles", "LF milk", "Banana", "tea", "honey"]
    assert pages.food_items("Cheese toastie with avocado") == ["Cheese toastie with avocado"]
    assert pages.short_food("Minestrone, sourdough") == "Minestrone + sourdough"
    summary = lambda *statuses: pages.symptoms_summary([{"symptom_status": s} for s in statuses])["value"]
    assert summary() == "Nothing logged" and summary("unrecorded", "unrecorded") == "Not yet recorded"
    assert summary("none", "unrecorded") == "1 to record" and summary("none") == "None so far"
    assert summary("none", "reported", "unrecorded") == "1 reported"


def test_today_page_timeline_tiles_and_sheet_data(client):
    day = today()
    put(client, day, "breakfast", food="Rice Bubbles, Lactose-free milk", time="08:00")
    put(client, day, "lunch", food="Cheese toastie", time="12:30", symptoms="Bloating · severity 2/5 (mild)", symptom_status="reported")
    put(client, day - timedelta(days=1), "dinner", food="Minestrone, sourdough", time="18:30")
    page = client.get("/").text
    assert "<h1>Today</h1>" in page and 'aria-current="date"' in page
    # Food shows as chips; symptoms show as written; a blank asks how you felt.
    assert '<li class="chip">Rice Bubbles</li><li class="chip">Lactose-free milk</li>' in page
    assert "Bloating · severity 2/5 (mild)" in page and "How did you feel after?" in page
    assert "<small>meals</small>" in page and "1 reported" in page and "Link Garmin" in page
    # Empty slots can be added, the Now line sits after the last meal, and yesterday's dinner can be copied.
    for label in ("Morning snack", "Afternoon snack", "Dinner", "Evening snack"):
        assert f'data-add="{pages.CATEGORIES and [k for k, v in pages.CATEGORIES.items() if v == label][0]}"' in page
    assert page.index('id="slot-lunch"') < page.index('class="now-row"') < page.index('id="slot-mid_afternoon_snack"')
    data = sheet_data(page)
    assert {"target": "dinner", "label": "Dinner", "food": "Minestrone, sourdough"} in data["copies"]
    assert {"target": "lunch", "label": "Dinner as today’s lunch", "food": "Minestrone, sourdough"} in data["copies"]
    assert data["suggestions"]["dinner"]["repeat"] == {"label": "Repeat yesterday's dinner", "food": "Minestrone, sourdough"}
    assert data["meals"]["lunch"]["symptom_status"] == "reported" and data["slots"]["mid_morning_snack"] == "Morning snack"
    # Another day: titled by weekday, no Now line, and the week strip moves by weeks.
    past = day - timedelta(days=9)
    other = client.get(f"/?date={past}").text
    assert f"<h1>{past:%A}</h1>" in other and 'class="now-row"' not in other and 'aria-label="Next week"' in other


def test_often_have_suggestions_need_repeats(client):
    day = today()
    for back in (1, 2, 3):
        put(client, day - timedelta(days=back), "breakfast", food="Oats, milk", time="08:00")
    put(client, day - timedelta(days=4), "breakfast", food="Toast", time="08:00")
    data = sheet_data(client.get("/").text)
    assert data["suggestions"]["breakfast"]["often"] == [{"label": "Oats + milk", "food": "Oats, milk"}]


def test_week_page_grid_counts_and_foods(client):
    day = today()
    put(client, day, "breakfast", food="Oats, milk", symptom_status="none")
    put(client, day - timedelta(days=1), "breakfast", food="Oats, banana", symptoms="Cramps", symptom_status="reported")
    put(client, day - timedelta(days=2), "dinner", food="Pasta")
    client.put(f"/api/days/{day - timedelta(days=2)}/notes", json={"text": "Late night", "expected_version": 0})
    put(client, day - timedelta(days=8), "lunch", food="Old soup")
    page = client.get("/week").text
    assert "<h1>This week</h1>" in page
    assert "3 <small>on 3 of 7 days</small>" in page and "2 <small>of 3 meals</small>" in page
    assert page.count('<span class="cell logged">') == 2 and page.count('<span class="cell symptom">') == 1
    assert "1 meal has no symptoms recorded" in page and f'href="/?date={day - timedelta(days=2)}"' in page
    assert "Oats</span>" in page and "<b>2</b>" in page and "Old soup" not in page
    assert page.count('class="wk-note"><svg') == 1
    earlier = client.get(f"/week?end={day - timedelta(days=7)}").text
    assert "<h1>Week</h1>" in earlier and "1 <small>on 1 of 7 days</small>" in earlier and 'aria-label="Next week"' in earlier


def test_report_page_includes_and_flags_unrecorded(client):
    day = today()
    put(client, day - timedelta(days=1), "lunch", food="Sandwich", time="12:30")
    put(client, day - timedelta(days=1), "breakfast", food="Oats", time="07:45", symptom_status="none")
    client.put(f"/api/days/{day - timedelta(days=1)}/notes", json={"text": "Private detail", "expected_version": 0})
    page = client.get("/report").text
    assert "Food &amp; symptom diary" in page and "2 meals" in page
    assert page.index("07:45") < page.index("12:30")  # in the order eaten
    assert 'class="unrecorded">Not recorded' in page and "<td class=\"\">None</td>" in page
    assert "1 of 2 meals have no symptoms recorded" in page
    assert "Private detail" not in page  # notes are off by default
    assert "Private detail" in client.get("/report?notes=1").text
    hidden = client.get("/report?symptoms=0").text
    assert "Not recorded</td>" not in hidden and "<th scope=\"col\">Symptoms</th>" not in hidden
    empty = client.get(f"/report?start={day - timedelta(days=40)}&end={day - timedelta(days=35)}").text
    assert "Nothing logged in these dates." in empty and 'id="print-report" disabled' in empty
    assert client.get("/report?start=2026-01-01&end=2026-12-31").status_code == 200


def test_pages_need_sign_in(app):
    from fastapi.testclient import TestClient
    with TestClient(app, base_url="http://localhost:8000") as anonymous:
        for path in ("/", "/week", "/report", "/health"):
            response = anonymous.get(path, follow_redirects=False)
            assert response.status_code in (302, 303) and "/login" in response.headers["location"]
