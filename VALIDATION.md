# Validation

Verified locally on 24 September 2026 with Python 3.13 and headless Chrome.

- 18 integration tests passed. Coverage includes schema migration, private endpoints, CSRF, field validation, meal/note conflict checks, restart persistence, date-boundary import rules, atomic and repeatable JSON imports, export round trips, safe HTML rendering, OAuth metadata, PKCE, single-use codes, token rotation/revocation, password-change invalidation, and MCP initialize/list/read/write calls against the same database as the UI.
- Browser checks passed: six meal slots, import review, food-only copying, save/reload, explicit no-symptoms selection, failed-save recovery, delayed-save field protection, accessible meal descriptions, stale-note conflicts, settings and sign-out. No JavaScript errors or horizontal overflow at 320, 390, 760, 1024 and 1440 CSS pixels.
- Desktop, mobile, mobile editor and mobile settings screenshots were inspected. The independent finish review scored its three findings resolved: save-time edit protection, accessible summaries and accurate mobile navigation documentation.
- PostgreSQL migration SQL compiled successfully using the PostgreSQL dialect. The integration suite used isolated SQLite databases. A live Railway Postgres instance was not available for connection testing.
- The deployed ChatGPT connection still requires validation using the user's actual HTTPS domain and account. Local MCP protocol tests are not a claim of an established ChatGPT connection.
- The design detector ran in degraded regex mode and found no matches. Its computed-style/contrast pass was unavailable; the finish review separately calculated the principal text-color pairs.

Browser test screenshots under `artifacts/` contain synthetic data and are excluded from Git/Docker. Original source diaries and prepared history under `private/` are separate and excluded too. No synthetic entries were added to the local personal diary or to Railway.

To repeat: install `requirements-dev.txt`, run `python -B -m pytest -q`, then `python -B tests/browser_check.py`. The browser script uses installed Chrome or Edge and an isolated temporary database. One upstream Starlette warning recommends its newer test HTTP client; it does not affect the passing integration checks.
