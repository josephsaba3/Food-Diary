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
| `GARMIN_SYNC_HOURS` | Optional. Hours between automatic Garmin syncs; default `3`, `0` turns it off |

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
- `sync_garmin`: download Garmin health data (via your Personal API key) for up to 31 days into the diary.
- `get_health_day` / `get_health_range`: cached daily health summaries and workouts (range up to 31 days); `include_raw` adds Garmin's own fields for a day.
- `get_activities`: cached workouts between two dates (up to a year).
- `download_garmin_history`: start a background download of up to two years (for example the last 6 months); progress appears in `get_garmin_status`.

The write tools explicitly require reading existing data first and retaining the user's details. They do not infer symptoms, severity or causal relationships. Deleting meals is available in the diary UI; no deletion tool is exposed to Claude. Use Settings → Disconnect all connections to revoke all MCP access. Changing the app password also invalidates existing login sessions and access/refresh tokens.

## Garmin health data

The diary pulls data from a Garmin watch (set up for a Forerunner 265; any Garmin Connect device works): steps, distance, floors, calories, resting/min/max heart rate, intensity minutes, stress, Body Battery, sleep stages and score, overnight HRV, SpO2, respiration, VO2 max and every recorded workout. Each day appears under the meals as **From your Garmin**, and Claude reads it through the MCP tools above. For example: “How did I sleep the nights after I had dairy?” or “Sync my Garmin for the last week and summarise my Body Battery.”

It uses Garmin's official [Connect Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api/), which gives you read-only access to your own data:

1. In Garmin Connect on the web, open the [Personal API Key page](https://connect.garmin.com/app/settings/personalApiToken), select **Create Key**, accept the terms and choose an expiry (30 days to one year). Copy the key; Garmin shows it only once.
2. In the diary, open Settings → Garmin, paste the key and choose **Link Garmin**. The diary exchanges the key for an access token, which lasts up to 24 hours and is renewed automatically, the same way as [Garmin's Python client](https://github.com/garmin/connect-personal-api-python). The key and token are stored encrypted with a key derived from `SESSION_SECRET`. If you rotate that secret, paste the key again.
3. To bring in history, choose a period under **Download the last** (up to 2 years) and select **Download**. It runs in the background in 31-day chunks, newest days first, and Settings and the Health page show its progress. While Garmin is linked, the server also re-syncs yesterday and today every `GARMIN_SYNC_HOURS` hours (default 3; `0` turns it off).

The **Health** page (the Diary | Health switch in the header, or **See how this compares over 30 days** under a day's Garmin section) shows 7, 30 or 90 days, or any custom range up to 93 days. It includes watch coverage, averages over the days the watch was worn (a day counts as worn when it has a resting heart rate), day-by-day charts for steps, resting heart rate, stress and Body Battery, and a daily log. Selecting a day opens a sheet with that day's details and a link to its meals. The page's **Sync** downloads the period shown.

Garmin emails you about a week before the key expires. Create a new key and paste it under **Replace API key**. Unlinking removes the key from the diary but keeps the health data already synced. To revoke the key itself, delete it in Garmin Connect. Re-syncing a day replaces its cached copy, and workouts deleted in Garmin Connect disappear at the next sync of their dates. Each day also keeps the trimmed Garmin responses, so `get_health_day` with `include_raw` can answer questions about fields the summary doesn't show. Training readiness isn't offered by the Personal API.

## Import the supplied diary

The local `private/` directory contains preserved copies of the supplied Markdown files and a prepared `history-2026-09-23-24.json` file. In Settings → Import entries, choose that JSON file. It contains seven meals across the two dates and daily notes retaining symptoms and unassigned snacks without inventing a time or meal category. The second day's “same as” foods are resolved from the first day's entries, with the additions retained. No unknown quantities, severity or symptom-free periods have been invented.

Private source files are intentionally excluded from Git and Docker. Upload the prepared JSON from your computer to the deployed app when ready. Repeating an import skips existing meals and notes. JSON exports use the same format and round-trip times, status, symptoms and notes. Instructions contained in the original diary documents have not been treated as executable app instructions.

## Validation and limits

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -B -m pytest -q
```

Tests cover database migrations, authentication, CSRF, validation, save conflicts, import rules, JSON round trips, OAuth/PKCE replay protection, refresh rotation, revocation, and real MCP JSON-RPC calls sharing the UI database. `tests/test_garmin.py` covers linking with a Personal API key, encrypted key/token storage, token reuse and renewal, activity paging, syncing, error reporting and the health MCP tools against a fake Personal API; no real Garmin account is contacted. `tests/browser_check.py` exercises the mobile UI against an isolated temporary database (outside Windows, set `CHROME_PATH` or let Playwright use its own Chromium) and saves desktop/mobile screenshots under `artifacts/`.

The app requires an internet connection to save. Failed saves keep the open form, but unsaved drafts are not stored offline. Browser navigation warns about unsaved changes. This is a personal diary, with no multi-user accounts, nutritional analysis, medical interpretation or Word-template exporter. Garmin numbers are shown as the watch recorded them; the app draws no health conclusions from them. Railway PostgreSQL and the final Claude account connection need a live deployment to verify; local tests do not imply either has been deployed.
