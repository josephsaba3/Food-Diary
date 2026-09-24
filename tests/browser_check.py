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
            chrome = Path(os.environ["PROGRAMFILES"]) / "Google/Chrome/Application/chrome.exe"
            edge = Path(os.environ["PROGRAMFILES(X86)"]) / "Microsoft/Edge/Application/msedge.exe"
            executable = chrome if chrome.exists() else edge
            browser = p.chromium.launch(executable_path=str(executable), headless=True)
            context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=1)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/?date=2026-09-24")
            page.get_by_label("Diary password").fill(settings.password)
            page.get_by_role("button", name="Open my diary").click()
            page.wait_for_url("**/?date=2026-09-24")
            assert page.locator(".meal-row").count() == 6
            assert page.locator("[data-import]").count() == 3
            page.locator('[data-import="lunch"]').click()
            page.wait_for_selector("dialog[open]")
            assert page.get_by_label("What did you eat or drink?").input_value() == "Mushroom risotto"
            assert page.locator("#meal-time").input_value() == ""
            assert page.get_by_role("radio", name="Not recorded").is_checked()
            assert page.locator("#meal-symptoms").input_value() == ""
            page.evaluate("async () => { await Promise.all(document.getAnimations().map(a => a.finished)); }")
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(artifacts / "mobile-editor.png"))
            page.get_by_label("Time Optional").fill("12:30")
            page.get_by_role("radio", name="No symptoms", exact=True).check()
            page.get_by_role("button", name="Save meal").click()
            page.wait_for_selector("dialog[open]", state="hidden")
            page.reload()
            assert "Mushroom risotto" in page.locator("#meal-lunch").inner_text()
            assert "12:30" in page.locator("#meal-lunch").inner_text()
            assert "Bloating" not in page.locator("#meal-lunch").inner_text()
            expect(page.get_by_role("button", name="Edit lunch", exact=True)).to_have_accessible_description(re.compile("Mushroom risotto.*12:30"))
            # An actual failed save must leave the draft open and readable.
            page.get_by_role("button", name="Add breakfast", exact=True).click()
            page.get_by_label("What did you eat or drink?").fill("Oats and coffee")
            page.route("**/api/days/*/meals/*", lambda route: route.abort())
            page.get_by_role("button", name="Save meal").click()
            page.locator("#meal-error").wait_for(state="visible")
            assert page.get_by_label("What did you eat or drink?").input_value() == "Oats and coffee"
            page.unroute("**/api/days/*/meals/*")
            pending = []
            page.route("**/api/days/*/meals/*", lambda route: pending.append(route))
            page.get_by_role("button", name="Save meal").click()
            expect(page.locator("#meal-food")).to_be_disabled()
            expect(page.locator("#meal-time")).to_be_disabled()
            expect(page.get_by_role("radio", name="Not recorded")).to_be_disabled()
            assert pending
            pending.pop().continue_()
            page.wait_for_selector("dialog[open]", state="hidden")
            page.unroute("**/api/days/*/meals/*")
            # Stale notes must not overwrite notes added by ChatGPT after the render.
            token = page.locator('meta[name="csrf-token"]').get_attribute("content")
            page.request.put(origin + "/api/days/2026-09-24/notes", data={"text": "Newer note from another device", "expected_version": 0}, headers={"X-CSRF-Token": token})
            page.get_by_role("textbox", name="Notes for the day", exact=True).fill("A stale browser note")
            page.get_by_role("button", name="Save notes", exact=True).click()
            page.locator("#notes-error").wait_for(state="visible")
            assert "elsewhere" in page.locator("#notes-error").inner_text()
            page.on("dialog", lambda dialog: dialog.accept())
            page.reload()
            page.get_by_role("textbox", name="Notes for the day", exact=True).fill("Felt comfortable after lunch.")
            page.get_by_role("button", name="Save notes", exact=True).click()
            expect(page.locator("#notes-status")).to_have_text("Saved")
            # Capture both required layouts, with fonts settled and no animation overlay.
            page.evaluate("document.fonts.ready")
            page.locator("#toast").evaluate("el => el.hidden = true")
            page.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
            page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
            for width in (320, 390, 760, 1024, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"overflow at {width}"
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(artifacts / "desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("link", name="Diary settings").click()
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
        print("Browser checks passed: import review, food-only copy, save/reload, failed-save recovery, note conflicts, settings and logout. No overflow at 320/390/760/1024/1440px; no JS errors.")


if __name__ == "__main__":
    run()
