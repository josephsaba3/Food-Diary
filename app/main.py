import json
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

from . import diary
from .config import Settings
from .database import make_engine, session_factory
from .importing import ImportBundle, import_bundle
from .mcp_server import build_mcp
from .models import AuthRecord
from .oauth import MCPAuthorization, OAuth
from .schemas import CATEGORIES, Category, MealInput, NoteInput
from .security import Security, local_next

ROOT = Path(__file__).parent


def create_app(settings=None):
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    sessions = session_factory(engine)
    security = Security(settings)
    templates = Jinja2Templates(directory=ROOT / "templates")
    oauth = OAuth(settings, sessions, security, templates)
    mcp = build_mcp(settings, sessions)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        engine.dispose()

    app = FastAPI(title="Food diary", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.sessions, app.state.engine, app.state.oauth = sessions, engine, oauth
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
        callback_origins = {"https://chatgpt.com"}
        callback_origins.update(f"{urlparse(uri).scheme}://{urlparse(uri).netloc}" for uri in settings.extra_redirect_uris)
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self' " + " ".join(sorted(callback_origins))
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

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

    @app.get("/")
    def index(request: Request, date: date | None = None):
        if redirect := require_page(request):
            return redirect
        day = date or today()
        with sessions() as db:
            data = diary.get_day(db, day)
        return templates.TemplateResponse(request, "diary.html", context(request, data=data, day=day,
            previous=day - timedelta(days=1), following=day + timedelta(days=1), categories=CATEGORIES))

    @app.get("/settings")
    def settings_page(request: Request):
        if redirect := require_page(request):
            return redirect
        return templates.TemplateResponse(request, "settings.html", context(request, mcp_url=oauth.resource,
            export_start=today() - timedelta(days=30)))

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
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    # Last mount: SDK serves the canonical /mcp path without a trailing slash redirect.
    app.mount("/", MCPAuthorization(mcp_app, oauth))
    return app
