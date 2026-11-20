# Food diary

A small, mobile-first, single-person diary built with FastAPI, server-rendered HTML, CSS and vanilla JavaScript. No React, npm build, paid API or OpenAI API key is needed.

The six daily meal slots hold a time, food/drink description and symptoms. Breakfast suggests yesterday's breakfast; lunch and dinner suggest yesterday's dinner. Importing opens an editable food-only draft. Time and symptoms belong to the new meal and are never copied. Snack slots remain manual.

Missing symptoms and an explicit report of no symptoms are stored separately. Daily notes hold symptoms between meals. Saves have version checks so a stale phone tab cannot overwrite a newer Claude change. There is one entry per category per date; edit it to add more detail.

## Run locally

Python 3.13 is recommended.

```powershell
py -3 -m venv --without-scm-ignore-files .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
py -3 scripts/setup_local.py
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python run.py
```

Open http://localhost:8000. Your generated local password is `APP_PASSWORD` in `.env`. You can change it there and restart. On macOS/Linux use `python3` and `.venv/bin/python`. If a Google Drive checkout blocks virtual-environment creation, put the virtual environment in a local temporary directory and run its Python from this project directory.

SQLite is for local use only. Railway production deliberately refuses to start without PostgreSQL, HTTPS and configured secrets.

## Railway

1. Put the project in a GitHub repository and create a Railway service from it. The Dockerfile and `railway.json` provide the build, pre-deploy migration and health check.
2. Add Railway Postgres. On the app service, reference its connection string as `DATABASE_URL=${{Postgres.DATABASE_URL}}` (use your database service's actual name).
3. Generate a public domain and configure these app variables:

| Variable | Value |
| --- | --- |
| `APP_ENV` | `production` |
| `DATABASE_URL` | Railway Postgres connection-string reference |
| `BASE_URL` | Exact public origin, e.g. `https://food-diary-production.up.railway.app`, without a trailing slash |
| `APP_PASSWORD` | A private password of at least 12 characters |
| `SESSION_SECRET` | A random secret of at least 32 characters |
| `TIMEZONE` | `Australia/Sydney` |
| `FORWARDED_ALLOW_IPS` | `*` when running exclusively behind Railway's trusted proxy |
| `GARMIN_SYNC_HOURS` | Optional. Hours between automatic Garmin syncs; default `4`, `0` turns it off |

Generate a session secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Railway supplies `PORT`; the server binds to `0.0.0.0`. Keep one app worker/replica: the login abuse limiter is per process. Postgres stores the diary, OAuth registrations and hashed connection tokens. Enable Railway database backups; the authenticated JSON export is also available in Settings.

The pre-deploy command runs `alembic upgrade head`; it never seeds diary entries. `/healthz` checks database access and migration-table availability. The app does not need a persistent filesystem volume in production. Local `.env`, databases, source diaries, screenshots and test artifacts are excluded from Git and Docker.

Deployment follows the [Railway FastAPI guide](https://docs.railway.com/guides/fastapi) and [FastAPI container guidance](https://fastapi.tiangolo.com/deployment/docker/). This repository is prepared for deployment; it does not itself create or modify a Railway project.

## Connect Claude

The remote MCP endpoint is **`https://YOUR-DOMAIN/mcp`**, using Streamable HTTP and OAuth authorization-code + PKCE. The app includes its own single-owner authorization server, a consent screen, discovery metadata, dynamic client registration, short-lived access tokens and rotating refresh tokens. Read/write access requires signing in with your diary password. A normal browser session alone cannot call MCP.

In Claude (web, desktop or mobile), open Settings → Connectors → Add custom connector and paste the server URL. Leave the OAuth client ID and secret blank; Claude registers itself dynamically. You will be sent to this diary's login/consent screen. Approve it, then test a request such as “What did I eat yesterday?” or “Record breakfast today at 8 am: oats and coffee. No symptoms.” See [Claude's connector authentication guide](https://claude.com/docs/connectors/building/authentication).

The registered callback must be Claude's `https://claude.ai/api/mcp/auth_callback` (or its announced successor `https://claude.com/api/mcp/auth_callback`). ChatGPT callbacks are no longer accepted, and clients registered with them lose access. For another MCP client, explicitly add its exact callback to `OAUTH_REDIRECT_URIS` (comma-separated). No wildcard callbacks or arbitrary redirect hosts are accepted. Local HTTP callbacks are accepted only for explicitly configured localhost URLs in development. Keep `BASE_URL` accurate: it is the OAuth issuer and the basis of the protected resource identifier. Tokens for another resource are rejected.

Available tools:

- `get_diary_today`: current Sydney date/time (or configured timezone).
- `get_diary_day`: meals, daily notes, versions and previous-day suggestions.
- `get_diary_range`: up to 31 inclusive days.
- `save_diary_meal`: create or replace a complete meal with `expected_version` (0 for new).
- `save_diary_notes`: create or replace notes with `expected_version`.

- `get_garmin_status`: whether a Garmin watch is linked, last sync, cached days.
- `sync_garmin`: download Garmin health data for up to 31 days into the diary.
- `get_health_day` / `get_health_range`: cached daily health summaries and workouts (range up to 31 days).
- `get_activities`: cached workouts between two dates (up to a year).

The write tools explicitly require reading existing data first and retaining the user's details. They do not infer symptoms, severity or causal relationships. Deleting meals is available in the diary UI; no deletion tool is exposed to Claude. Use Settings → Disconnect all connections to revoke all MCP access. Changing the app password also invalidates existing login sessions and access/refresh tokens.

## Garmin health data

The diary can pull data from a Garmin watch (built and tested for a Forerunner 265, but any Connect device works): steps, distance, floors, calories, resting/min/max heart rate, intensity minutes, stress, Body Battery, sleep stages and score, overnight HRV, SpO2, respiration, training readiness, VO2 max and every recorded workout. Each day appears under the meals as **From your Garmin**, and Claude can read it through the MCP tools above, for example “How did I sleep the nights after I had dairy?” or “Sync my Garmin for the last week and summarise my Body Battery.”

Garmin offers no personal API keys. Its official Health API is only for approved business partners. The diary instead signs in the way the Garmin Connect app does, using the maintained [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library:

1. Open Settings → Garmin and enter your Garmin Connect email and password. If your account uses two-step verification, enter the code Garmin sends.
2. The password is used only for that sign-in and is never stored. The diary keeps Garmin's refreshable session token, encrypted with a key derived from `SESSION_SECRET` (rotating that secret means linking Garmin again).
3. Choose **Sync now** to backfill up to 90 days, or use the **Sync** button on any day. While Garmin is linked, the server also re-syncs yesterday and today every `GARMIN_SYNC_HOURS` hours (default 4; `0` turns it off).

Garmin sometimes blocks sign-ins from cloud hosts such as Railway. If linking fails there, run `pip install garminconnect` and `python scripts/garmin_tokens.py` on your own computer, then paste what it prints under **Sign-in blocked? Paste a token instead**. Treat that token like a password.

Unlinking forgets the token but keeps the health data already synced. Re-syncing a day replaces its cached copy, and workouts deleted in Garmin Connect disappear at the next sync of their dates. Because this uses Garmin's private app API, a change on Garmin's side can break syncing until the library is updated; a sync error then appears in Settings.

## Import the supplied diary

The local `private/` directory contains preserved copies of the supplied Markdown files and a prepared `history-2026-09-23-24.json` file. In Settings → Import entries, choose that JSON file. It contains seven meals across the two dates and daily notes retaining symptoms and unassigned snacks without inventing a time or meal category. The second day's “same as” foods are resolved from the first day's entries, with the additions retained. No unknown quantities, severity or symptom-free periods have been invented.

Private source files are intentionally excluded from Git and Docker. Upload the prepared JSON from your computer to the deployed app when ready. Repeating an import skips existing meals and notes. JSON exports use the same format and round-trip times, status, symptoms and notes. Instructions contained in the original diary documents have not been treated as executable app instructions.

## Validation and limits

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -B -m pytest -q
```

Tests cover database migrations, authentication, CSRF, validation, save conflicts, import rules, JSON round trips, OAuth/PKCE replay protection, refresh rotation, revocation, and real MCP JSON-RPC calls sharing the UI database. `tests/test_garmin.py` covers Garmin linking (password, two-step code and pasted token), encrypted token storage and refresh, syncing, error reporting and the health MCP tools against a fake Garmin client; no real Garmin account is contacted. `tests/browser_check.py` exercises the mobile UI against an isolated temporary database (outside Windows, set `CHROME_PATH` or let Playwright use its own Chromium) and saves desktop/mobile screenshots under `artifacts/`.

The app requires an internet connection to save. Failed saves keep the open form, but unsaved drafts are not stored offline. Browser navigation warns about unsaved changes. This is a personal diary, with no multi-user accounts, nutritional analysis, medical interpretation or Word-template exporter. Garmin numbers are shown as the watch recorded them; the app draws no health conclusions from them. Railway PostgreSQL and the final Claude account connection need a live deployment to verify; local tests do not imply either has been deployed.
