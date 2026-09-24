# Food diary

A small, mobile-first, single-person diary built with FastAPI, server-rendered HTML, CSS and vanilla JavaScript. No React, npm build, paid API or OpenAI API key is needed.

The six daily meal slots hold a time, food/drink description and symptoms. Breakfast suggests yesterday's breakfast; lunch and dinner suggest yesterday's dinner. Importing opens an editable food-only draft. Time and symptoms belong to the new meal and are never copied. Snack slots remain manual.

Missing symptoms and an explicit report of no symptoms are stored separately. Daily notes hold symptoms between meals. Saves have version checks so a stale phone tab cannot overwrite a newer ChatGPT change. There is one entry per category per date; edit it to add more detail.

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

Generate a session secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Railway supplies `PORT`; the server binds to `0.0.0.0`. Keep one app worker/replica: the login abuse limiter is per process. Postgres stores the diary, OAuth registrations and hashed connection tokens. Enable Railway database backups; the authenticated JSON export is also available in Settings.

The pre-deploy command runs `alembic upgrade head`; it never seeds diary entries. `/healthz` checks database access and migration-table availability. The app does not need a persistent filesystem volume in production. Local `.env`, databases, source diaries, screenshots and test artifacts are excluded from Git and Docker.

Deployment follows the [Railway FastAPI guide](https://docs.railway.com/guides/fastapi) and [FastAPI container guidance](https://fastapi.tiangolo.com/deployment/docker/). This repository is prepared for deployment; it does not itself create or modify a Railway project.

## Connect ChatGPT

The remote MCP endpoint is **`https://YOUR-DOMAIN/mcp`**, using Streamable HTTP and OAuth authorization-code + PKCE. The app includes its own single-owner authorization server, a consent screen, discovery metadata, dynamic client registration, short-lived access tokens and rotating refresh tokens. Read/write access requires signing in with your diary password. A normal browser session alone cannot call MCP.

In a ChatGPT account with developer-mode/custom-MCP access, add the server URL with OAuth authentication. Use dynamic registration (no manually supplied client ID or secret). You will be sent to this diary's login/consent screen. Approve it, then test a request such as “What did I eat yesterday?” or “Record breakfast today at 8 am: oats and coffee. No symptoms.” Account access and the exact settings UI depend on your ChatGPT plan/workspace. See [OpenAI's connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt) and [OAuth requirements](https://developers.openai.com/plugins/build/auth).

The registered callback must be the official stable ChatGPT callback `https://chatgpt.com/connector_platform_oauth_redirect`, or a ChatGPT `/aip/<id>/oauth/callback` URL. For another MCP client, explicitly add its exact callback to `OAUTH_REDIRECT_URIS` (comma-separated). No wildcard callbacks or arbitrary redirect hosts are accepted. Local HTTP callbacks are accepted only for explicitly configured localhost URLs in development. Keep `BASE_URL` accurate: it is the OAuth issuer and the basis of the protected resource identifier. Tokens for another resource are rejected.

Available tools:

- `get_diary_today`: current Sydney date/time (or configured timezone).
- `get_diary_day`: meals, daily notes, versions and previous-day suggestions.
- `get_diary_range`: up to 31 inclusive days.
- `save_diary_meal`: create or replace a complete meal with `expected_version` (0 for new).
- `save_diary_notes`: create or replace notes with `expected_version`.

The write tools explicitly require reading existing data first and retaining the user's details. They do not infer symptoms, severity or causal relationships. Deleting meals is available in the diary UI; no deletion tool is exposed to ChatGPT. Use Settings → Disconnect all connections to revoke all MCP access. Changing the app password also invalidates existing login sessions and access/refresh tokens.

## Import the supplied diary

The local `private/` directory contains preserved copies of the supplied Markdown files and a prepared `history-2026-09-23-24.json` file. In Settings → Import entries, choose that JSON file. It contains seven meals across the two dates and daily notes retaining symptoms and unassigned snacks without inventing a time or meal category. The second day's “same as” foods are resolved from the first day's entries, with the additions retained. No unknown quantities, severity or symptom-free periods have been invented.

Private source files are intentionally excluded from Git and Docker. Upload the prepared JSON from your computer to the deployed app when ready. Repeating an import skips existing meals and notes. JSON exports use the same format and round-trip times, status, symptoms and notes. Instructions contained in the original diary documents have not been treated as executable app instructions.

## Validation and limits

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -B -m pytest -q
```

Tests cover database migrations, authentication, CSRF, validation, save conflicts, import rules, JSON round trips, OAuth/PKCE replay protection, refresh rotation, revocation, and real MCP JSON-RPC calls sharing the UI database. `tests/browser_check.py` exercises the mobile UI against an isolated temporary database and saves desktop/mobile screenshots under `artifacts/`.

The app requires an internet connection to save. Failed saves keep the open form, but unsaved drafts are not stored offline. Browser navigation warns about unsaved changes. This is a personal diary, with no multi-user accounts, nutritional analysis, medical interpretation or Word-template exporter. Railway PostgreSQL and the final ChatGPT account connection need a live deployment to verify; local tests do not imply either has been deployed.
