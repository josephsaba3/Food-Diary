"""Run against an isolated DB; never adds synthetic entries to the user's diary."""
import os
import re
import socket
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn
from alembic import command
from alembic.config import Config
from playwright.sync_api import expect, sync_playwright

from app.config import Settings
from app.main import create_app
from app import diary
from app.schemas import MealInput


def run():
    with tempfile.TemporaryDirectory(prefix="food-diary-browser-", ignore_cleanup_errors=True) as temp, ExitStack() as cleanup:
        url = "sqlite:///" + (Path(temp) / "diary.db").as_posix()
        os.environ["DATABASE_URL"] = url
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        settings = Settings(url, "browser-check-password", "b" * 40, origin)
        app = create_app(settings)
        with app.state.sessions() as db:
            diary.save_meal(db, date(2026, 9, 23), "breakfast", MealInput(food="Instant oats, lactose-free milk and honey", time="08:00", expected_version=0))
            diary.save_meal(db, date(2026, 9, 23), "dinner", MealInput(food="Mushroom risotto", time="19:00", symptoms="Bloating at 9 pm", expected_version=0))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        def stop_server():
            server.should_exit = True
            thread.join(timeout=5)
            app.state.engine.dispose()
        cleanup.callback(stop_server)
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        artifacts = ROOT / "artifacts"
        artifacts.mkdir(exist_ok=True)
        with sync_playwright() as p:
            chrome = Path(os.environ.get("PROGRAMFILES", "/nonexistent")) / "Google/Chrome/Application/chrome.exe"
            edge = Path(os.environ.get("PROGRAMFILES(X86)", "/nonexistent")) / "Microsoft/Edge/Application/msedge.exe"
            executable = next((str(path) for path in (chrome, edge) if path.exists()), os.environ.get("CHROME_PATH"))
            # Outside Windows, set CHROME_PATH or let Playwright use its own Chromium.
            browser = p.chromium.launch(executable_path=executable, headless=True)
            context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=1)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/?date=2026-09-24")
            page.get_by_label("Diary password").fill(settings.password)
            page.get_by_role("button", name="Open my diary").click()
            page.wait_for_url("**/?date=2026-09-24")
            assert page.locator(".tl-row").count() == 6 and page.locator(".empty-slot").count() == 6
            # Copy from yesterday: food only, never yesterday's time or symptoms.
            page.get_by_text("Copy from yesterday").click()
            assert page.locator("[data-copy]").count() == 3
            page.get_by_role("menuitem", name=re.compile("Dinner as today")).click()
            page.wait_for_selector("#log-sheet[open]")
            assert page.locator("#food-chips .chip").all_inner_texts()[0].startswith("Mushroom risotto")
            assert page.locator('[data-slot="lunch"]').get_attribute("aria-pressed") == "true"
            assert page.locator("#log-time").input_value() == "12:30"  # lunch's usual time on a past day
            assert page.get_by_role("radio", name="Not now").get_attribute("aria-checked") == "true"
            page.evaluate("async () => { await Promise.all(document.getAnimations().map(a => a.finished)); }")
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(artifacts / "mobile-editor.png"))
            page.locator("#log-time").fill("12:45")
            page.get_by_role("radio", name="No symptoms").click()
            page.get_by_role("button", name="Save to lunch").click()
            lunch = page.locator("#slot-lunch.has-meal")
            lunch.wait_for()
            assert "Mushroom risotto" in lunch.inner_text() and "12:45" in lunch.inner_text()
            assert "No symptoms" in lunch.inner_text() and "Bloating" not in lunch.inner_text()
            # Symptom shortcuts write free text, and a failed save leaves the draft open and readable.
            page.locator('[data-add="breakfast"]').click()
            page.wait_for_selector("#log-sheet[open]")
            assert page.locator("#log-time").input_value() == "08:00"
            page.locator("#food-input").fill("Oats and coffee")
            page.get_by_role("radio", name="Had symptoms").click()
            page.locator('[data-symptom="Bloating"]').click()
            page.locator('[data-sev="2"]').click()
            assert page.locator("#symptom-preview").inner_text() == "Bloating · severity 2/5 (mild)"
            page.route("**/api/days/*/meals/*", lambda route: route.abort())
            page.get_by_role("button", name="Save to breakfast").click()
            page.locator("#log-error").wait_for(state="visible")
            assert page.locator("#food-chips .chip").all_inner_texts()[0].startswith("Oats and coffee")
            page.unroute("**/api/days/*/meals/*")
            pending = []
            page.route("**/api/days/*/meals/*", lambda route: pending.append(route))
            page.get_by_role("button", name="Save to breakfast").click()
            expect(page.locator("#food-input")).to_be_disabled()
            expect(page.locator("#log-time")).to_be_disabled()
            assert pending
            pending.pop().continue_()
            page.wait_for_load_state("load")
            page.locator("#slot-breakfast .symptom-note").wait_for()
            assert "Bloating · severity 2/5 (mild)" in page.locator("#slot-breakfast").inner_text()
            page.unroute("**/api/days/*/meals/*")
            # Stale notes must not overwrite notes added by Claude after the render.
            token = page.locator('meta[name="csrf-token"]').get_attribute("content")
            page.request.put(origin + "/api/days/2026-09-24/notes", data={"text": "Newer note from another device", "expected_version": 0}, headers={"X-CSRF-Token": token})
            page.locator("#open-notes").click()
            page.locator("#day-notes").fill("A stale browser note")
            page.get_by_role("button", name="Save notes", exact=True).click()
            page.locator("#notes-error").wait_for(state="visible")
            assert "elsewhere" in page.locator("#notes-error").inner_text()
            page.on("dialog", lambda dialog: dialog.accept())
            page.reload()
            page.locator("#open-notes").click()
            page.locator("#day-notes").fill("Felt comfortable after lunch.")
            page.get_by_role("button", name="Save notes", exact=True).click()
            page.wait_for_selector("#notes-sheet[open]", state="hidden")
            assert page.locator("#open-notes").inner_text() == "Felt comfortable after lunch."
            # Capture both layouts, with fonts settled, and check every page for overflow.
            page.evaluate("document.fonts.ready")
            page.locator("#toast").evaluate("el => el.hidden = true")
            page.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
            page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
            for path in ("/?date=2026-09-24", "/week?end=2026-09-24", "/report?start=2026-09-20&end=2026-09-24&notes=1", "/health"):
                page.goto(origin + path)
                for width in (320, 390, 760, 1024, 1440):
                    page.set_viewport_size({"width": width, "height": 900})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"overflow at {width} on {path}"
            page.goto(origin + "/report?start=2026-09-20&end=2026-09-24")
            assert "Mushroom risotto" in page.locator(".report-doc").inner_text()
            page.goto(origin + "/?date=2026-09-24")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(artifacts / "desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("link", name="Settings").first.click()
            assert page.get_by_label("Server URL").input_value() == origin + "/mcp"
            for width in (320, 390):
                page.set_viewport_size({"width": width, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"settings overflow at {width}"
            page.screenshot(path=str(artifacts / "mobile-settings.png"), full_page=True)
            page.get_by_role("button", name="Sign out", exact=True).click()
            page.wait_for_url("**/login")
            assert not errors, errors
            browser.close()
        server.should_exit = True
        thread.join(timeout=5)
        print("Browser checks passed: copy from yesterday (food only), log sheet save/reload, symptom shortcuts, failed-save recovery, note conflicts, settings and logout. No overflow on Today, Week, Report or Health at 320/390/760/1024/1440px; no JS errors.")


if __name__ == "__main__":
    run()
