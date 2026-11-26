import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from starlette.middleware.sessions import SessionMiddleware

from . import diary, garmin, health_view, pages
from .config import Settings
from .database import make_engine, session_factory
from .importing import ImportBundle, import_bundle
from .garmin import GarminError, GarminLink
from .mcp_server import build_mcp
from .models import AuthRecord
from .oauth import MCPAuthorization, OAuth
from .push import Push
from .schemas import (CATEGORIES, Category, GarminAccountCode, GarminAccountLogin, GarminHistory, GarminKey, GarminSync,
                      MealInput, NoteInput, PushEndpoint, PushSubscribe)
from .security import Security, local_next

ROOT = Path(__file__).parent
log = logging.getLogger("food_diary.garmin")


def create_app(settings=None, garmin_http=None, push_http=None):
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    sessions = session_factory(engine)
    security = Security(settings)
    templates = Jinja2Templates(directory=ROOT / "templates")
    # Version asset URLs by content so phones never pair a fresh page with stale CSS or JS.
    templates.env.globals["asset"] = {name: f"/static/{name}?v={hashlib.sha256((ROOT / 'static' / name).read_bytes()).hexdigest()[:12]}"
                                      for name in ("app.css", "app.js")}
    oauth = OAuth(settings, sessions, security, templates)
    link = GarminLink(settings, sessions, garmin_http)
    push = Push(settings, sessions, push_http)
    templates.env.filters["hm"] = lambda minutes: f"{int(minutes) // 60}h {int(minutes) % 60:02d}m" if minutes is not None else ""
    templates.env.filters["localtime"] = lambda iso: datetime.fromisoformat(iso).astimezone(ZoneInfo(settings.timezone)).strftime("%d %b, %-I:%M %p").lstrip("0") if iso else ""
    templates.env.filters["label"] = lambda value: str(value or "").replace("_", " ").capitalize()
    templates.env.filters["clock"] = lambda iso: datetime.fromisoformat(iso).astimezone(ZoneInfo(settings.timezone)).strftime("%H:%M") if iso else ""
    templates.env.globals["timedelta_7"] = timedelta(days=7)
    templates.env.globals["days_ago"] = {n: timedelta(days=n - 1) for n in (7, 14, 28)}
    mcp = build_mcp(settings, sessions, link)
    mcp_app = mcp.streamable_http_app()

    async def garmin_auto_sync():
        # Keep yesterday (finished sleep/HRV) and today fresh without anyone pressing Sync.
        await asyncio.sleep(30)
        while True:
            try:
                if await asyncio.to_thread(link.connected):
                    day = today()
                    await asyncio.to_thread(link.sync, day - timedelta(days=1), day)
            except Exception as error:  # never let a Garmin hiccup stop the loop
                log.warning("Automatic Garmin sync failed: %s", error)
            await asyncio.sleep(settings.garmin_sync_hours * 3600)

    def check_stress_now(sync=True):
        """Bring today in from Garmin (unless a sync is already running) and alert on a new high-stress stretch."""
        now = now_local()
        if sync and link.connected():
            try:
                link.sync(now.date(), now.date())
            except GarminError as error:  # e.g. a sync already running: check what's stored
                log.info("Stress check sync skipped: %s", error)
        with sessions() as db:
            stored = garmin.health_timeline(db, now.date())
        return push.check_stress(now.date(), stored["slots"], stored["events"], now.hour * 60 + now.minute)

    async def stress_watch():
        # While a phone has alerts on, look at today's stress every few minutes during waking hours.
        await asyncio.sleep(90)
        while True:
            try:
                if 6 <= now_local().hour < 23 and await asyncio.to_thread(push.has_subscribers):
                    await asyncio.to_thread(check_stress_now)
            except Exception as error:  # never let one bad check stop the loop
                log.warning("Stress check failed: %s", error)
            await asyncio.sleep(settings.stress_check_minutes * 60)

    @asynccontextmanager
    async def lifespan(app):
        tasks = [asyncio.create_task(garmin_auto_sync())] if settings.garmin_sync_hours > 0 else []
        if settings.stress_check_minutes > 0:
            tasks.append(asyncio.create_task(stress_watch()))
        async with mcp.session_manager.run():
            yield
        for task in tasks:
            task.cancel()
        engine.dispose()

    app = FastAPI(title="Food diary", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.sessions, app.state.engine, app.state.oauth, app.state.garmin = sessions, engine, oauth, link
    app.state.push, app.state.check_stress = push, check_stress_now
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="food_diary",
        max_age=30 * 86400, same_site="lax", https_only=settings.production)

    @app.middleware("http")
    async def headers(request, call_next):
        try:
            length = int(request.headers.get("content-length", "0") or "0")
        except ValueError:
            return JSONResponse({"detail": "Invalid content length."}, status_code=400)
        if length > 2_000_000:
            return JSONResponse({"detail": "File is too large. Maximum size is 2 MB."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        callback_origins = {"https://claude.ai", "https://claude.com"}
        callback_origins.update(f"{urlparse(uri).scheme}://{urlparse(uri).netloc}" for uri in settings.extra_redirect_uris)
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self' " + " ".join(sorted(callback_origins))
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(GarminError)
    async def garmin_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(diary.ConflictError)
    async def conflict_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    def today():
        return datetime.now(ZoneInfo(settings.timezone)).date()

    def require_page(request):
        if not security.authenticated(request):
            return RedirectResponse("/login?" + urlencode({"next": request.url.path + ("?" + request.url.query if request.url.query else "")}), status_code=303)

    def context(request, **values):
        return {"csrf": security.csrf(request), "today": today(), "timezone": settings.timezone, **values}

    @app.get("/healthz")
    def health():
        try:
            with engine.connect() as db:
                db.execute(text("SELECT 1 FROM alembic_version"))
            return {"status": "ok"}
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.get("/login")
    def login_page(request: Request, next: str = "/"):
        return templates.TemplateResponse(request, "login.html", context(request, next=local_next(next)))

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        security.check_csrf(request, form.get("csrf"))
        security.rate_limit(request, "login")
        next_path = local_next(str(form.get("next", "/")))
        if not security.password_ok(str(form.get("password", ""))):
            return templates.TemplateResponse(request, "login.html", context(request, next=next_path,
                error="That password doesn’t match. Please try again."), status_code=401)
        request.session.clear()
        request.session["user"] = security.stamp
        security.csrf(request)
        return RedirectResponse(next_path, status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        form = await request.form()
        security.check_csrf(request, form.get("csrf"))
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    def now_local():
        return datetime.now(ZoneInfo(settings.timezone))

    @app.get("/")
    def index(request: Request, date: date | None = None):
        if redirect := require_page(request):
            return redirect
        day = date or today()
        with sessions() as db:
            data = diary.get_day(db, day)
            health = garmin.health_day(db, day)
            slots = garmin.health_timeline(db, day)["slots"]
            view = pages.today_view(db, day, today(), now_local().strftime("%H:%M"), data["meals"], health["summary"], slots)
            sheet = {"day": day.isoformat(), "today": today().isoformat(), "now": now_local().strftime("%H:%M"),
                     "slots": CATEGORIES, "meals": data["meals"], "note": data["note"],
                     "suggestions": pages.suggestions(db, day, today()), "copies": pages.copy_choices(db, day)}
        return templates.TemplateResponse(request, "today.html", context(request, data=data, day=day, view=view, sheet=sheet,
            section="today", title=pages.day_label(day, today()) if day >= today() - timedelta(days=1) else f"{day:%A}",
            date_label=pages.long_date(day), garmin_connected=link.connected()))

    @app.get("/week")
    def week_page(request: Request, end: date | None = None):
        if redirect := require_page(request):
            return redirect
        last = min(end or today(), today())
        with sessions() as db:
            view = pages.week_view(db, last, today())
        return templates.TemplateResponse(request, "week.html", context(request, view=view, section="week"))

    @app.get("/report")
    def report_page(request: Request, start: date | None = None, end: date | None = None, meals: int = 1,
                    symptoms: int = 1, notes: int = 0, watch: int = 0):
        if redirect := require_page(request):
            return redirect
        last = min(end or today(), today())
        first = min(start or last - timedelta(days=6), last)
        if (last - first).days > 92:
            first = last - timedelta(days=92)
        include = {"meals": True, "symptoms": bool(symptoms), "notes": bool(notes), "watch": bool(watch)}
        with sessions() as db:
            view = pages.report_view(db, first, last, include, garmin.health_range(db, first, last) if watch else [])
        return templates.TemplateResponse(request, "report.html", context(request, view=view, include=include, section="report"))

    @app.get("/health")
    def health_page(request: Request, period: int | None = None, start: date | None = None, end: date | None = None,
                    day: date | None = None):
        if redirect := require_page(request):
            return redirect
        first, last, key = health_view.resolve_period(today(), period, start, end)
        with sessions() as db:
            view = health_view.build(garmin.health_range(db, first, last), first, last, today())
            if day and first <= day <= last:
                view["selected"] = (day - first).days
            # One day shows every 15 minutes; longer periods show day by day.
            tl = timeline_view(db, first, view["days"][0]["summary"]) if view["n"] == 1 else None
        status = link.status()
        return templates.TemplateResponse(request, "health.html", context(request, view=view, period=key, section="health",
            garmin=status, garmin_connected=status["connected"], tl=tl, limited="limiting" in (status["last_error"] or "")))

    def timeline_view(db, day, summary):
        stored = garmin.health_timeline(db, day)
        now = now_local()
        return health_view.day_chart(day, stored["slots"], garmin.activities_between(db, day, day),
                                     diary.get_day(db, day)["meals"], stored["events"], summary,
                                     now.hour * 60 + now.minute if day == now.date() else None, CATEGORIES)

    @app.get("/settings")
    def settings_page(request: Request):
        if redirect := require_page(request):
            return redirect
        return templates.TemplateResponse(request, "settings.html", context(request, mcp_url=oauth.resource,
            export_start=today() - timedelta(days=30), garmin=link.status(), alerts=push.status(), section="settings"))

    @app.get("/api/days/{day}", dependencies=[Depends(security.require_user)])
    def read_day(day: date):
        with sessions() as db:
            return diary.get_day(db, day)

    @app.put("/api/days/{day}/meals/{category}", dependencies=[Depends(security.check_write)])
    def put_meal(day: date, category: Category, entry: MealInput):
        with sessions() as db:
            return diary.save_meal(db, day, category.value, entry)

    @app.delete("/api/days/{day}/meals/{category}", dependencies=[Depends(security.check_write)])
    def remove_meal(day: date, category: Category, version: int):
        with sessions() as db:
            diary.delete_meal(db, day, category.value, version)
        return Response(status_code=204)

    @app.put("/api/days/{day}/notes", dependencies=[Depends(security.check_write)])
    def put_note(day: date, note: NoteInput):
        with sessions() as db:
            return diary.save_note(db, day, note)

    @app.get("/api/health/{day}", dependencies=[Depends(security.require_user)])
    def read_health(day: date):
        with sessions() as db:
            return garmin.health_day(db, day)

    @app.get("/api/garmin", dependencies=[Depends(security.require_user)])
    def garmin_status():
        return link.status()

    # Plain `def` routes run in FastAPI's thread pool, so slow Garmin calls never block the event loop.
    @app.post("/api/garmin/key", dependencies=[Depends(security.check_write)])
    def garmin_key(request: Request, body: GarminKey):
        security.rate_limit(request, "garmin", limit=6)
        return link.save_key(body.api_key)

    @app.post("/api/garmin/sync", dependencies=[Depends(security.check_write)])
    def garmin_sync(body: GarminSync):
        end = body.end or today()
        start = body.start or end - timedelta(days=body.days - 1)
        return link.sync(start, end, max_days=92)

    @app.post("/api/garmin/history", dependencies=[Depends(security.check_write)])
    def garmin_history(body: GarminHistory):
        end = body.end or today()
        start = body.start or end - timedelta(days=body.days - 1)
        return link.start_job(start, end)

    @app.get("/api/garmin/history", dependencies=[Depends(security.require_user)])
    def garmin_history_status():
        return link.job_status()

    # Optional Garmin account sign-in for steps and stress every 15 minutes (not in the Personal API).
    @app.post("/api/garmin/account/login", dependencies=[Depends(security.check_write)])
    def garmin_account_login(request: Request, body: GarminAccountLogin):
        security.rate_limit(request, "garmin-account", limit=5)
        return link.account_login(body.email, body.password)

    @app.post("/api/garmin/account/mfa", dependencies=[Depends(security.check_write)])
    def garmin_account_mfa(request: Request, body: GarminAccountCode):
        security.rate_limit(request, "garmin-account-mfa", limit=6)
        return link.account_mfa(body.code)

    @app.post("/api/garmin/account/unlink", dependencies=[Depends(security.check_write)])
    def garmin_account_unlink():
        return link.account_unlink()

    @app.post("/api/garmin/disconnect", dependencies=[Depends(security.check_write)])
    def garmin_disconnect():
        return link.disconnect()

    @app.get("/api/push", dependencies=[Depends(security.require_user)])
    def push_status():
        return push.status()

    @app.post("/api/push/subscribe", dependencies=[Depends(security.check_write)])
    def push_subscribe(body: PushSubscribe):
        return push.subscribe(body.endpoint, body.keys.p256dh, body.keys.auth, body.device)

    @app.post("/api/push/unsubscribe", dependencies=[Depends(security.check_write)])
    def push_unsubscribe(body: PushEndpoint):
        return push.unsubscribe(body.endpoint)

    @app.post("/api/push/test", dependencies=[Depends(security.check_write)])
    def push_test():
        return push.test()

    @app.get("/export", dependencies=[Depends(security.require_user)])
    def export(start: date, end: date):
        with sessions() as db:
            try:
                days = diary.export_days(db, start, end)
            except ValueError as error:
                raise HTTPException(422, str(error))
        content = {"format": "food-diary-v1", "timezone": settings.timezone, "days": [
            {"date": d["date"], "meals": list(d["meals"].values()), "notes": d["note"]["text"]} for d in days]}
        return Response(json.dumps(content, indent=2, ensure_ascii=False), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="food-diary-{start}-{end}.json"'})

    @app.post("/api/import", dependencies=[Depends(security.check_write)])
    async def import_data(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 2_000_000:
                raise HTTPException(413, "File is too large. Maximum size is 2 MB.")
        try:
            bundle = ImportBundle.model_validate_json(raw)
            with sessions() as db:
                result = import_bundle(db, bundle)
            return result
        except (ValidationError, ValueError) as error:
            raise HTTPException(422, "The file could not be imported. Check the dates, categories and symptom fields in the JSON export.") from error
        except IntegrityError as error:
            raise diary.ConflictError("The diary changed during import. Try again; existing meals will be skipped.") from error

    @app.post("/api/disconnect", dependencies=[Depends(security.check_write)])
    def disconnect():
        with sessions.begin() as db:
            db.execute(delete(AuthRecord).where(AuthRecord.kind.in_(["access", "refresh", "code", "pending"])))
        return {"disconnected": True}

    oauth.install(app)
    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        # Served from the root so the worker can cover the whole app.
        return Response((ROOT / "static" / "sw.js").read_bytes(), media_type="text/javascript")

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    # Last mount: SDK serves the canonical /mcp path without a trailing slash redirect.
    app.mount("/", MCPAuthorization(mcp_app, oauth))
    return app
