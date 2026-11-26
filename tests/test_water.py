from datetime import date, timedelta

from tests.test_app import app, client, meal  # noqa: F401  (fixtures)

URL = "/api/days/2026-09-24/water/"


def test_water_adds_up_per_slot_and_never_goes_negative(client):
    assert client.post(URL + "mid_afternoon_snack", json={"ml": 250}).json() == {
        "date": "2026-09-24", "category": "mid_afternoon_snack", "ml": 250, "total_ml": 250}
    assert client.post(URL + "mid_afternoon_snack", json={"ml": 250}).json()["ml"] == 500
    assert client.post(URL + "lunch", json={"ml": 250}).json()["total_ml"] == 750
    assert client.post(URL + "lunch", json={"ml": -250}).json() == {"date": "2026-09-24", "category": "lunch", "ml": 0, "total_ml": 500}
    assert client.post(URL + "lunch", json={"ml": -250}).json()["ml"] == 0  # stays at 0
    assert client.post(URL + "dinner", json={"ml": -250}).json()["ml"] == 0  # taking from nothing
    for _ in range(25):
        client.post(URL + "dinner", json={"ml": 250})
    assert client.post(URL + "dinner", json={"ml": 250}).json()["ml"] == 5000  # capped per slot
    day = client.get("/api/days/2026-09-24").json()
    assert day["water"] == {"mid_afternoon_snack": 500, "dinner": 5000} and day["water_total_ml"] == 5500
    assert client.post(URL + "lunch", json={"ml": 0}).status_code == 422
    assert client.post(URL + "brunch", json={"ml": 250}).status_code == 422
    assert client.post(f"/api/days/{date.today() + timedelta(days=2)}/water/lunch", json={"ml": 250}).status_code == 400
    client.headers.pop("X-CSRF-Token")
    assert client.post(URL + "lunch", json={"ml": 250}).status_code == 403


def test_today_page_shows_water_buttons_and_totals(client):
    page = client.get("/?date=2026-09-24").text
    assert page.count('data-water-step="250"') == 6 and "No water yet" in page
    client.put("/api/days/2026-09-24/meals/lunch", json=meal("Salad"))
    client.post(URL + "lunch", json={"ml": 750})
    client.post(URL + "mid_afternoon_snack", json={"ml": 500})
    page = client.get("/?date=2026-09-24").text
    assert page.count('data-water-step="250"') == 6 and "1.25 L" in page and 'data-ml="750"' in page and "750 ml" in page


def test_water_exports_imports_and_reports(client):
    client.put("/api/days/2026-09-24/meals/lunch", json=meal("Salad"))
    client.post(URL + "lunch", json={"ml": 500})
    client.post(URL + "evening_snack", json={"ml": 250})
    exported = client.get("/export?start=2026-09-24&end=2026-09-24").json()
    assert exported["days"][0]["water"] == {"lunch": 500, "evening_snack": 250}
    report = client.get("/report?start=2026-09-24&end=2026-09-24").text
    assert "750 ml (lunch 500 ml, evening snack 250 ml)" in report
    # Importing again keeps what's there; a new day's water comes in.
    exported["days"].append({"date": "2026-09-23", "meals": [], "notes": "", "water": {"breakfast": 250}})
    assert client.post("/api/import", json=exported).json()["water_added"] == 1
    assert client.get("/api/days/2026-09-24").json()["water"] == {"lunch": 500, "evening_snack": 250}
    assert client.get("/api/days/2026-09-23").json()["water"] == {"breakfast": 250}
