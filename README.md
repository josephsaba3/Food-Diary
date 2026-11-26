# Food diary

A small, mobile-first, single-person diary built with FastAPI, server-rendered HTML, CSS and vanilla JavaScript. No React, npm build, paid API or OpenAI API key is needed.

The app has five screens, with a bottom tab bar on phones and side navigation on a computer:

- **Today**: the week at a glance (a dot for each day with meals, amber when symptoms were reported), tiles for meals logged, whether symptoms are recorded, and steps, then the day as a timeline. Logged meals show their time, food as chips, the symptoms as written (or **How did you feel after?** when they're not recorded yet) and, when the watch measured it, the heart-rate peak in the 90 minutes after. Empty slots can be tapped to add, a **Now** line marks the present, and **Copy from yesterday** offers yesterday's meals (and yesterday's dinner as today's lunch). **Other info** holds the day's notes. Every slot, logged or not, has a **+ 250 ml** water button (and **−** to undo); the day's total shows beside **Meals & drinks**, and the report and exports include water per slot. Claude can log water too (`add_diary_water`).
- **Log sheet** (the green **+**): choose the meal slot, time and date, add food as chips (with **You often have** and **Repeat … yesterday's/Monday's …** shortcuts), and say how you felt: **Not now** (not recorded), **No symptoms**, or **Had symptoms**, with symptom, severity (0–5) and Bristol stool-form shortcuts that write free text such as "Bloating, cramps · severity 2/5 (mild) · Bristol type 5 · started an hour after". Copying a meal only copies food, never its time or symptoms.
- **Week**: seven days by six meal slots (logged, empty, with symptoms), how many meals have symptoms recorded with a shortcut to fill the gaps, and the foods eaten most.
- **Health**: Garmin data (see below).
- **Report**: a dietitian's report for a date range, with meals in the order eaten, symptoms as written (blanks print as "not recorded"), and optional day notes and watch figures. **Print or save as PDF** uses the device's print menu.

Missing symptoms and an explicit report of no symptoms are stored separately. Saves have version checks so a stale phone tab cannot overwrite a newer Claude change. There is one entry per meal slot per date; edit it to add more detail.

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
| `STRESS_CHECK_MINUTES` | Optional. While a phone has stress alerts on, minutes between checks of today's stress (6 am–11 pm); default `30`, minimum `15`, `0` turns alerts off |
| `STRESS_ALERT_LEVEL` | Optional. Stress level that counts as high; default `76`, where Garmin's high range starts |
| `STRESS_ALERT_MINUTES` | Optional. Minutes of high stress in a row before an alert; default `30` |

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
- `get_health_timeline`: one day's heart rate (average, lowest, highest), Body Battery and, where Garmin shares them, stress and steps in 15-minute slots of the watch's local time, plus Garmin's timed stress and recovery periods and that day's workouts; optionally only between two times (for example the hours after a meal).
- `download_garmin_history`: start a background download of up to two years (for example the last 6 months); progress appears in `get_garmin_status`.

The write tools explicitly require reading existing data first and retaining the user's details. They do not infer symptoms, severity or causal relationships. Deleting meals is available in the diary UI; no deletion tool is exposed to Claude. Use Settings → Disconnect all connections to revoke all MCP access. Changing the app password also invalidates existing login sessions and access/refresh tokens.

## Garmin health data

The diary pulls data from a Garmin watch (set up for a Forerunner 265; any Garmin Connect device works): steps, distance, floors, calories, resting/min/max heart rate, intensity minutes, stress, Body Battery, sleep stages and score, overnight HRV, SpO2, respiration, VO2 max and every recorded workout. Today shows the day's steps and heart-rate notes beside meals, and Claude reads it all through the MCP tools above. The Health page charts steps, heart rate, stress and Body Battery day by day for a period, and every 15 minutes for a single day (**Day**, or **Through the day** from a day's card), with workouts and logged meal times marked and every slot listed. Heart rate and Body Battery (the lowest and highest reading in each 15 minutes) come from Garmin's Personal API, as do stress and recovery periods from the Body Battery report. The Personal API answers HTTP 404 for steps and stress levels every 15 minutes, so those come from an optional **Garmin account sign-in** (Settings → Garmin → Steps and stress every 15 minutes): your Garmin Connect email and password, plus the code Garmin sends if your account uses two-step verification. The password goes to Garmin once and is never stored; the diary keeps Garmin's sign-in token, Fernet-encrypted like the API key, and saves it again when Garmin refreshes it. It uses the open-source [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library, the same sign-in as other open-source Garmin tools; Garmin doesn't support it and may block or change it, and it may refuse sign-ins from cloud servers. If Garmin signs the diary out, syncs carry on with the API key and Settings says to sign in again. Without the sign-in, those charts show the day's figures with a note. Settings → Garmin → 15-minute data from Garmin shows what Garmin last answered for each kind. Days synced before a kind of 15-minute data was added are fetched again by the next download. For example: “How did I sleep the nights after I had dairy?” or “Sync my Garmin for the last week and summarise my Body Battery.”

It uses Garmin's official [Connect Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api/), which gives you read-only access to your own data:

1. In Garmin Connect on the web, open the [Personal API Key page](https://connect.garmin.com/app/settings/personalApiToken), select **Create Key**, accept the terms and choose an expiry (30 days to one year). Copy the key; Garmin shows it only once.
2. In the diary, open Settings → Garmin, paste the key and choose **Link Garmin**. The diary exchanges the key for an access token, which lasts up to 24 hours and is renewed automatically, the same way as [Garmin's Python client](https://github.com/garmin/connect-personal-api-python). The key and token are stored encrypted with a key derived from `SESSION_SECRET`. If you rotate that secret, paste the key again.
3. To bring in history, choose a period under **Download the last** (up to 2 years) and select **Download**. It runs in the background a week at a time, newest days first, spacing requests about a second apart (`GARMIN_REQUEST_GAP`) and pausing between weeks (`GARMIN_BATCH_PAUSE`, default 20 seconds) so Garmin's rate limit isn't hit. If Garmin still says to slow down, the download waits (1, 2, 4, 8 then 15 minutes) and carries on from the same day; Settings and the Health page show its progress and when it will resume. Days already downloaded in full (synced after 6 am the next morning, with their 15-minute data) are skipped, so downloading a period again only fetches what's new. While Garmin is linked, the server also re-syncs yesterday and today every `GARMIN_SYNC_HOURS` hours (default 3; `0` turns it off).

The **Health** page shows a day, week, month or 90 days, or any custom range up to 93 days. A single day has tiles for steps (against your goal), resting heart rate, stress and Body Battery, then **Your day with meals**: heart rate, stress, steps and Body Battery on one time axis over waking hours, with your meals marked; picking a meal shows what the watch measured in the two hours after it next to its symptoms. **More from your watch** lists sleep, HRV, workouts, calories, breathing and SpO2. Longer periods show averages over the days the watch was worn (a day counts as worn when it has a resting heart rate), day-by-day charts and a daily log; selecting a day opens its details. **Sync** downloads the period shown.

Garmin emails you about a week before the key expires. Create a new key and paste it under **Replace API key**. Unlinking removes the key from the diary but keeps the health data already synced. To revoke the key itself, delete it in Garmin Connect. Re-syncing a day replaces its cached copy, and workouts deleted in Garmin Connect disappear at the next sync of their dates. Each day also keeps the trimmed Garmin responses, so `get_health_day` with `include_raw` can answer questions about fields the summary doesn't show. Training readiness isn't offered by the Personal API.

### High stress: flags and phone alerts

On a single day, stress at or above 76 (Garmin's high range) is drawn in red, each stretch of it is bracketed above the stress lane, and the most stressed 15 minutes is labelled like the heart rate peak. The stress tile shows Garmin's minutes of high stress for the day.

**Settings → Stress alerts → Turn on alerts on this phone** sends a notification when the watch reads high stress for 30 minutes in a row (`STRESS_ALERT_LEVEL`, `STRESS_ALERT_MINUTES`). It uses Web Push, which needs no account or keys to set up: the signing key is derived from `SESSION_SECRET` (changing it means turning alerts on again). On Android it works in Chrome or the installed app; on iPhone the diary must be added to the Home Screen and opened from there (iOS 16.4 or later). While at least one phone has alerts on, the server syncs today from Garmin every `STRESS_CHECK_MINUTES` minutes between 6 am and 11 pm and alerts once per stretch, at most every two hours. Stress every 15 minutes needs the Garmin account sign-in; without it, alerts use Garmin's own stress periods from Body Battery. Readings only reach Garmin when the watch syncs with the Garmin Connect app, so alerts trail the watch by however long that takes.

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
