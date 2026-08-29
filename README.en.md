[Korean](README.md) | [English](README.en.md)

# Stock Trading Journal - Multi-User Edition

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A **local-based stock trading journal and portfolio management web application** for individual investors.
Built on a Python (Flask) backend and SQLite, it features a **multi-user environment** and **mobile optimization**, allowing you to safely and comfortably manage your trading records anytime, anywhere.

---

## Table of Contents
1. [Overview & Objective](#1-overview--objective)
2. [Key Features](#2-key-features)
3. [Prerequisites](#3-prerequisites)
4. [Installation & Execution](#4-installation--execution)
5. [Backup & Security](#5-backup--security)
6. [Project Structure](#6-project-structure)
7. [Trading API](#7-trading-api)
8. [Testing](#8-testing)

---

## 1. Overview & Objective

This program is a tool designed to help individual investors **systematically and intuitively manage their trading journals and portfolios via a web browser**, replacing traditional Excel sheets or handwritten notes.
Beyond simple recording, it aims to maximize investment review and strategy formulation by providing features such as **performance analytics (win rate, profit factor, etc.), a calendar view, and real-time price & news integration**.

---

## 2. Key Features

*   **Multi-User & Security**
    *   Provides sign-up and login features. The first registered user is automatically assigned as the super admin, who can then approve or deny subsequent registrations.
    *   Account blocking for 1 minute after 5 failed login attempts, and automatic logout after 1 hour of inactivity with a 5-minute warning.
    *   Secure financial data protection against large file uploads (16MB limit) and implements XSS & CSRF security measures.
*   **Journal & Smart Editor**
    *   Allows logging of buy, sell, observe, and dividend records, as well as general memos and ideas about specific stocks.
    *   Supports direct image insertion into the editor body (clipboard pasting of screenshots, drag & drop) and resizing. It also automatically cleans up colors/background styles when pasting external text.
*   **Portfolio Dashboard & Real-time Prices**
    *   Automatically calculates and visually displays currently held stocks, total investment amount, average unit price, and cumulative realized profit/loss using a pie chart. (Supports custom drag & drop sorting)
    *   Supports automatic real-time price updates every minute, perfectly handling not only regular market hours (KRX) but also **after-hours single-price trading (NXT)** caching and toggling.
    *   Dashboard view options (e.g., showing closed stocks, viewing current prices) are synchronized with each user's preferences.
*   **5-Layer Advanced Filtering**
    *   Quickly and precisely filter and analyze vast trading records through 5 independent filters: by record type, stock, account type, broker, and sub-account.
*   **Performance Analytics & Chart View**
    *   Provides in-depth statistical metrics for review, such as win rate, profit factor, average profit/loss, maximum drawdown (MDD), and average holding period.
    *   Supports interactive bar charts showing monthly/weekly realized profit/loss, evaluated profit/loss, trading volume, and cumulative profit flows. Clicking on a chart bar directly reveals the detailed trading history for that period.
*   **Data Integrity**
    *   Ensures the integrity of average unit prices and realized profits by fundamentally blocking logically incorrect data inputs at the server level, such as selling more quantity than held or selling non-existent stocks.
*   **Calendar View & Export**
    *   Intuitively grasp daily trading/memo status and realized profits by color themes on a monthly calendar, and export the entire history as an Excel (XLSX) file with a single click.
*   **Live News Integration**
    *   Loads the latest news related to currently held or recently traded stocks via Google News (RSS) every 5 minutes and displays them in the sidebar.
*   **Admin Dashboard & System Logging**
    *   Provides a dedicated admin dashboard to easily view the user list, sign-up/recent login times, data statistics, and manage user statuses.
    *   Access, operation, and error logs are automatically rotated and recorded in daily files (`logs/backend_app_*.log`) for easy maintenance.
*   **Mobile Optimization (PWA Ready)**
    *   Offers a pleasant UX that acts like a native app when added to the home screen on iOS/Android, with responsive UI for desktop/mobile and support for dark/light themes.

---

## 3. Prerequisites

*   Python 3.11 or higher
*   Modern web browser (Chrome, Safari, Edge, etc. recommended)
*   (Optional) `ngrok`, `Cloudflare Tunnels`, or `tmux` for external access
*   (Development) Node 18+ to run the tests — used by the `calc.js` unit tests and the frontend/backend parity check

---

## 4. Installation & Execution

### Running the Server
Navigate to the project folder and start the local server using the provided script.
If required packages are missing, the script asks to install them using the version ranges in `requirements.txt`.

**Mac / Linux Environment**
Grant execution permission once, then run it conveniently as a shell script:
```bash
chmod +x run.sh
./run.sh
```
*(You can also run it using the traditional command `python backend_app.py`)*

**Windows Environment**
```bash
python backend_app.py
```

### Access
Open a web browser and navigate to the following address:
```text
http://127.0.0.1:9094
```

---

## 5. Backup & Security

### Data Backup & Restoration
*   **Easy Web Backup (Recommended)**: Clicking the **[Full Backup]** button at the top of the app screen downloads a ZIP file containing the logged-in account's entire DB (`journal.db`) and attached images, complete with integrity verification. Uploading this ZIP file using the **[Restore]** button in a new environment restores the exact previous state.
*   **Automatic Backup**: Every midnight, a compressed backup file for each user is automatically generated in the `backup/` folder on the server, and a self-integrity check (CRC, record count) is performed to ensure 100% restorability.
*   **Manual Backup**: If you are migrating the server manually, simply copy the `db/` and `uploads/` folders within the project.

### Security Guide
This application handles sensitive personal financial and investment data, so extra caution is required.
*   **Super Admin Account**: The very first account registered after installing the app is automatically designated as the super admin. Any subsequent users must be approved by this admin to log in.
*   **Session Security**: Internally implements security cookies to prevent session hijacking (XSS) and cross-site request forgery (CSRF). It also features automatic logout after 1 hour of inactivity to prevent data leaks on public devices.
*   **External Access Caution**: Direct external network (HTTP) exposure via router port forwarding is not recommended. For secure external access (e.g., from a smartphone), please use encrypted security tunneling services like `ngrok`, `Cloudflare Tunnels`, or `Tailscale`.
*   **Production Environment (HTTPS)**: While it runs stably on a local network via `waitress`, integrating a reverse proxy (e.g., Nginx) to apply HTTPS (SSL) certificates is highly recommended for proper web publishing. When serving over HTTPS, set `SESSION_COOKIE_SECURE=1` so the session cookie never travels over plaintext.
*   **Static file exposure**: Only `static/` and your own `uploads/<username>/` are web-reachable. The database, backups, logs, source files, and `.secret_key` cannot be fetched by URL even by a logged-in user. (The project root used to be fully exposed — see [Design Rules](#6-project-structure).)
*   **Response headers**: Every response carries a CSP (Content-Security-Policy), `X-Frame-Options`, `X-Content-Type-Options`, and `Referrer-Policy`; HTTPS requests also get HSTS. Adding a new CDN requires adding its origin to the CSP list in `app/routes/middleware.py` — otherwise the browser blocks it silently.
*   **Brute-force defense**: Per-IP request limits and per-account login lockout work together (`app/utils/ratelimit.py`). Their state lives in process memory, so **run a single process** for the intended limits to hold.

### Password Recovery
The web-based admin reset only works **while an admin is already logged in**, which is useless when the admin themselves is locked out. In that case, log into the server and run the recovery tool. It works even when the app is not running and even when the account is locked; changes take effect immediately, without restarting the server.

```bash
cd ~/GitHub/stock-memo
./.venv/bin/python tools/reset_password.py --list               # list accounts and their status
./.venv/bin/python tools/reset_password.py --user <name>        # type a new password
./.venv/bin/python tools/reset_password.py --user <name> --random   # issue a random temporary password
./.venv/bin/python tools/reset_password.py --user <name> --unlock   # also clear a locked/unapproved state
```

The password is never echoed and never lands in shell history. Only the fact of the reset is appended to `logs/backend_app.log` — never the password itself. Note that failed logins now log the reason (unknown account vs. password mismatch) along with the DB path in use, so checking the log first is the fastest route to a diagnosis.

---

## 6. Project Structure

```text
stock-memo/
├── backend_app.py      # App assembly — wires modules, initializes schema, bootstrap()
├── wsgi.py             # gunicorn/uwsgi entrypoint (calls bootstrap)
├── config.py           # Paths, constants, secret key (single source of settings)
│
├── app/                # Application code, grouped by role
│   │
│   │  ── routes: request layer (Flask blueprints) ───────────────
│   ├── routes/
│   │   ├── auth.py         # Login, signup, logout, reset requests + session checks
│   │   ├── authz.py        # Permission rule (admin_required) — registers no routes,
│   │   │                   #   so route modules never depend on each other
│   │   ├── admin.py        # Admin — approve/delete accounts, reset passwords
│   │   ├── api.py          # Screen API — entry CRUD, stats, prices, news, bot controls
│   │   ├── backup_api.py   # Full backup ZIP export / restore
│   │   └── middleware.py   # Security/cache headers, CSP, gzip, global error handling
│   │
│   │  ── services: domain layer (Flask-independent, unit-testable) ─
│   ├── services/
│   │   ├── accounts.py     # Account-number normalization + account mapping storage
│   │   ├── stats.py        # Trade performance analytics (pure functions)
│   │   ├── prices.py       # Price lookup service (per-provider with fallbacks)
│   │   ├── news.py         # Ticker news lookup (Google News RSS + cache)
│   │   ├── backups.py      # Backup ZIP integrity verification (pure functions)
│   │   ├── images.py       # Inline base64 image extraction / attachment storage
│   │   ├── users.py        # Username rules, password policy, session epoch, paths
│   │   └── jobs.py         # Background threads (auto backup, NXT close caching)
│   │
│   │  ── database: data access and schema ───────────────────────
│   ├── database/
│   │   ├── db.py           # SQLite connections (PRAGMAs applied consistently)
│   │   ├── schema.py       # DB schema single source — every table, column, index
│   │   └── entry_logic.py  # Entry persistence / integrity checks + INSERT column source
│   │
│   │  ── utils: cross-cutting concerns ──────────────────────────
│   └── utils/
│       ├── applog.py       # Logging setup (daily rotation, single-line format)
│       ├── ratelimit.py    # IP request limits + account login lockout
│       ├── memcache.py     # TTL in-memory cache (prices/news) + process-local state list
│       └── statscache.py   # Stats cache and data version (ETag)
│
├── trading_api/        # Trading bot REST API (/api/v1/*) — auth, idempotency
│   ├── common.py       #   Blueprint, constants, time utils (no deps — others lean on it)
│   ├── keys.py         #   API key hashing, issuance, revocation
│   ├── security.py     #   Token signing/verification, scopes, rate limits
│   ├── validation.py   #   Input normalization and shape validation
│   ├── entries.py      #   Input → entries row → response, idempotent INSERT
│   ├── bots.py         #   Bot registration, status, downstream command queue
│   └── routes.py       #   HTTP handlers (assembly only)
│
│  ── Frontend ───────────────────────────────────────────────────
├── templates/          # Login, signup, and main screen HTML
├── static/
│   ├── style.css       #   Screen design and layout
│   ├── calc.js         #   Trading calculation single source (matches app/services/stats.py)
│   └── js/             #   Screen behavior, split by feature (loaded 01-, 02-, …)
│
├── tools/              # Operator/recovery scripts, run on the server (never web-exposed)
│   ├── reset_password.py   # CLI to recover a locked-out login
│   ├── update_holidays.sh  # Refresh the `holidays` package (weekly cron recommended)
│   └── stock-memo          # Server start/stop wrapper
│
├── run.sh              # Automated launch script (Mac/Linux)
├── pyproject.toml      # Lint (ruff) rules — pinned so tool defaults can't drift them
├── requirements.txt    # Runtime dependencies (version ranges pinned)
├── requirements-dev.txt#  Test and lint dependencies
├── UniversalTradingHistoryAPI.json  # Trading bot API contract (OpenAPI 3.1)
├── backup/             # Daily per-user backup archives
├── db/journal.db       # Trading journal database (SQLite, auto-created)
├── logs/               # Application logs
└── uploads/            # Attached image files
```

### Design Rules

Four rules keep this structure intact. Each one marks a place where something
actually went wrong before.

**1. Serve static files only from `static/`.**
The app used to run with `static_folder='.'`, which exposed the entire project root.
Any logged-in user could download `/.secret_key`, `/db/journal.db`, `/backup/*.zip`,
and other users' account mappings. A leaked secret key lets anyone forge a session
cookie and impersonate the admin, so this was privilege escalation, not just
information disclosure. Guarded by `test_sensitive_files_are_not_served`.

**2. `app/database/schema.py` is the only place that defines the DB schema.**
Add a new column to `ADDED_COLUMNS` *and* to the `CREATE TABLE` statement — the
first serves existing databases, the second serves fresh ones. `test_schema.py`
checks that a freshly created DB and a migrated legacy DB end up identical.

**3. Read paths as `config.<name>` at the point of use.**
Importing the name directly (`from config import UPLOAD_FOLDER`) captures a copy, so
tests that patch the path silently verify nothing.

**4. `import backend_app` must have no side effects.**
Schema setup, data migration, and background threads happen only when `bootstrap()`
is called explicitly. Guarded by `test_startup.py`.

**5. Stock identity is code-first, and codes are upper-cased on write.**
When the folding rule differs between layers, one stock becomes **one position on screen
and two in validation**. That is exactly what happened: stats, the UI, and quote lookups
upper-cased the code, while holdings matching and sell validation compared the stored
value verbatim. Invisible for 6-digit Korean codes — but for a foreign ticker the bot
sends as `aapl` and the user typed as `AAPL`, **a position you can see refuses to sell.**
`entry_logic.normalize_stock_code` owns the rule; both writes (`_value_for`, i.e. every
INSERT/UPDATE) and reads (comparisons) go through it. `stockIdentity` in `static/calc.js`
is the front-end counterpart. Rows already stored are folded at startup by
`migrate_stock_code_case`. Guarded by `test_entry_integrity.py` and `test_trading_api.py`.

**6. Never write `import app.x` — always `from app.x import y`.**
The package (`app`) and the Flask instance in `backend_app` (`app`) share a name. The
former form rebinds that name to the package and breaks any later `app.logger` or
`app.register_blueprint`. Worse, it can pass silently depending on import order.
`test_startup.py` scans the sources and rejects the form outright.

### Frontend script load order

The files in `static/js/` are **ordered classic scripts, not ES modules**. Top-level
`let`/`const`/`function` share one global lexical environment, so execution semantics
match the old single `script.js`. (Inline `onclick` handlers in the HTML call global
functions directly; switching to modules would break all of them.)

*   Load order is filename order, and `backend_app.app_scripts()` builds the list from
    the folder — the template never hardcodes `<script>` tags.
*   ⚠️ Never reference another file's function **at load time**. Function hoisting is
    per-file, so a top-level reference to a later file's function is a `ReferenceError`.
    Wrap event handlers as `() => fn()` so the lookup happens when the event fires.

> Performance metrics (win rate, profit factor, monthly aggregates) live in exactly
> one place: the backend `app/services/stats.py`. `static/calc.js` used to carry a copy of the same
> algorithm, but the app never called it — the analysis screen has always used
> `/api/stats` — so it was deleted. What remains in `static/calc.js` is only the
> state-transition function the screens use to roll up holdings (average cost, basis).
> Regressions are pinned by `tests/test_stats.py` against a golden snapshot
> (`tests/fixtures/stats_expected.json`).

### Deployment Shape

**This app assumes a single process.** The stats cache, rate limits, account lockout,
and price cache all live in process memory, so running multiple workers
(`gunicorn -w 2` or more) loosens the rate limits proportionally and desynchronizes
the caches.

*   The default (`python backend_app.py`) runs waitress with 16 threads in one process.
*   To use a WSGI server directly, point it at `wsgi:application` with a single worker.
*   If you must run multiple workers, let only one run the background jobs by setting
    `START_BACKGROUND_JOBS=0` on the others, and run the backup from cron instead.

---

## 7. Trading API

A REST API that lets an external trading bot (HTS) record its fills on this server in real time.
The full contract is defined in [`UniversalTradingHistoryAPI.json`](UniversalTradingHistoryAPI.json) (OpenAPI 3.1).

### Authentication

1. Issue a key from the web dashboard → Settings → **HTS API key** (it starts with `skm_`).
2. Exchange it for a 24-hour Access Token by calling `POST /api/v1/auth/token` with the key in the `X-API-KEY` header.
3. Send `Authorization: Bearer <token>` on every subsequent request.

> ⚠️ **The raw key is shown exactly once, at issue time.** Only a SHA-256 hash is stored, so it cannot be retrieved again. Revoking a key immediately invalidates every token minted from it.

Keys carry scopes and tokens inherit them — `trades:write` (create/update/delete records), `trades:read` (query and sync point), `bot:write` (bot ping).

### Main endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/health` | Health check (unauthenticated) |
| `POST` | `/api/v1/auth/token` | Exchange API key for an access token |
| `POST` | `/api/v1/trades` | Submit a single trade |
| `POST` | `/api/v1/trades/batch` | Bulk submit (up to 500, per-item results) |
| `GET` | `/api/v1/trades` | Query records (reconciliation, cursor pagination) |
| `GET` | `/api/v1/trades/last-sync` | Last sync point (used to compute the backfill window) |
| `GET` | `/api/v1/trades/by-exec-id/{id}` | Check existence by idempotency key |
| `PATCH` / `DELETE` | `/api/v1/trades/{id}` | Amend / delete a record |
| `POST` | `/api/v1/positions/opening` | Register opening balances at integration start |
| `POST` | `/api/v1/bot/status` | Bot heartbeat ping |

### Design principles

*   **Idempotency**: `brokerExecutionId` carries a UNIQUE constraint, so a duplicate resend returns the existing record with `200`. A bot that never saw a response can **always retry safely.** The recommended key is `{env}:{account}:{fillDate}:{orderNo}` — broker order numbers are reused every business day, so the order number alone is not allowed.
*   **Never lose a fill**: integrity violations such as overselling are **stored and flagged `needsReview`.** Returning `400` would make the bot retry forever and lose that fill permanently. (Manual entry through the web UI is still blocked, since a human can fix it on the spot.)
*   **Simulated vs. real**: separated by `isSimulated` and excluded from default queries and statistics.
*   **System vs. manual**: `isSystem` marks only orders the automated strategy placed. Bots report **every** fill in the account — including orders a human placed in a broker app — so without this split, manual trading merges into automated performance.
*   **Trade-date attribution**: `executedAt` (RFC3339 with offset) plus `exchange` yield the **exchange-local trade date**, so US after-hours fills do not slip into the next Korean date.
*   **Rate limits**: token issuance 10 per 5 min per IP; general API 600 per min per key. Exceeding either returns `429` + `Retry-After`.

### Trade classification

`isSystem=true` is recorded as `system`. When it is `false` or absent, the server **inherits the previous classification for that symbol**, falling back to empty. If the bot sets `tradeClass` explicitly, that value wins. Opening balances (`positions/opening`) are also `isSystem=false`.

**`system` is never inherited** — an older version stored every bot record that way, so inheriting would make the very contamination we are fixing permanent. Inheritance applies only to classifications a human deliberately chose (`long-term`, `dividend`, …).

> Past records already stored as `system` stay that way; idempotency means even a resync will not overwrite them. Fix them on the web.

### Multiple bots (botId)

Heartbeats and commands are scoped to the **user, not the API key** (the key becomes a username right after authentication). Issuing separate keys therefore does not separate instances — `botId` in the ping body does.

*   **Status**: the dashboard indicator follows the **worst bot.** "Green if any is alive" would let a simulated bot's ping mask a dead live bot.
*   **Commands**: a resync is queued against a specific bot. With more than one bot and no target, the server returns `400` — otherwise whichever bot pings first claims it and acks, showing "done" on screen: a **silent failure.**
*   **Ghost rows**: decommissioned machines leave stale rows. Since the indicator follows the worst bot, one such row pins the status to "disconnected" forever and **kills the real alarm signal.** Delete it with `✕` in the list (trade records are untouched; a running bot re-registers on its next ping).

### Resync — restoring deleted records

Deleting a record on the web does not make the bot resend it, because the bot only remembers that it *sent* the record. That is the correct behavior — otherwise a deliberately deleted record could never stay deleted.

Use **Settings → Account Settings → Resync** and pick quarter (90d) / half-year (180d) / year (365d), all **rolling** rather than calendar-based. Idempotency skips existing records as `duplicate`, so a result like `inserted=10, skipped=80` is itself **the answer to how much had been deleted.** Duplicates are free, so err on the side of a longer window. The API (`POST /api/me/bot/resync`) also accepts explicit `from`/`to`.

**Command delivery**: bots usually sit behind a home network, so the server cannot reach them first. Pressing the button queues a row in `bot_commands`, delivered on the bot's next ping (≤10s).

*   **At-most-once** — a missing ack is never retried. Redelivering would rerun the same resync if the bot restarts just before acking, resurrecting records deliberately deleted in between. Pressing the button again is far better.
*   A command shown as "done" never goes out again — the bot's duplicate guard lives only in memory, so the server owns this guarantee.
*   Unclaimed commands expire after an hour and show as "unprocessed". Bots reporting `status=stopped` are not given work.
*   A malformed ack still returns `200` for the ping itself; a `400` would break the heartbeat and flip the display to "disconnected".

**A resync is mandatory after restoring from backup.** The backup ZIP excludes `api_keys` and `users`, so on a new server you must reissue the API key and update the bot's `JOURNAL_API_KEY`. Records the bot sent after the backup are marked delivered in its queue, so backfill cannot recover them either. Resync treats the bot's local trade log as the source of truth and restores even records its queue has already pruned.

> ⚠️ **`pause`/`resume` are deliberately not implemented.** They exist in the spec enum but are excluded from `SUPPORTED_BOT_COMMANDS`, so queuing is refused outright. Resync means "resend data that is already the bot's", whereas `pause` grants **the web server authority to halt a trading bot** — a compromised web app could freeze the bot while it holds positions.

### Errors and operations

```json
{ "error": "human-readable message", "errorCode": "OVERSELL", "details": {} }
```

Always branch on `errorCode`. The `error` text may change without notice.

*   **Serve over HTTPS** so the API key is never transmitted in the clear (reverse proxy such as Nginx + SSL).
*   Rate limiting is in-process memory. Scaling to multiple processes requires swapping it for a shared store such as Redis.


---

## 8. Testing

Backend APIs, data integrity validations, backup restorations (round-trip), and performance analytics logic are verified by `pytest`-based unit tests.
Test codes are located in the `tests/` folder and use a temporary DB, ensuring the actual operational data (`db/journal.db`) remains unaffected.

```bash
# Install test dependencies (once)
pip install -r requirements-dev.txt
playwright install chromium      # for the browser E2E tests

# Run everything
pytest

# Fast run without browser E2E (about 5 seconds)
pytest --ignore=tests/test_frontend.py
```

The frontend holdings engine (`static/calc.js`) is verified by the built-in Node test runner. Backend performance metrics are pinned by `tests/test_stats.py` against a golden snapshot.

```bash
# Run frontend calculation unit tests (Node 18+)
node --test tests/calc.test.js

# Backend performance-metric regressions
pytest tests/test_stats.py
```

### Lint

```bash
pip install ruff
ruff check .
```

Rules live in `pyproject.toml`. Only the checks that catch real defects are enabled
(undefined/unused names, syntax errors, common traps) — turning everything on at once
would bury the signal under hundreds of style findings.

### Tests that protect the structure

Beyond ordinary feature tests, a few tests exist purely to keep the design rules above
from quietly eroding during refactors.

| Test | What it protects |
|---|---|
| `test_middleware.py::test_sensitive_files_are_not_served` | `.secret_key`, DB, and backups never leak through static routes |
| `test_middleware.py::test_every_app_script_is_listed_and_served` | No `static/js` fragment is dropped from the page, and order holds |
| `test_schema.py::test_migrated_legacy_db_matches_fresh_db` | A fresh DB and a migrated legacy DB have identical schemas |
| `test_admin.py::test_every_admin_route_is_permission_checked` | Every admin route returns 403 to non-admins |
| `test_startup.py::test_importing_backend_app_has_no_side_effects` | Importing the module creates no DB and starts no threads |
| `test_accounts.py::test_trading_api_shares_the_same_rule` | Bot API and web UI use the same account-normalization rule |
| `test_frontend.py::test_broker_dropdown_is_built_from_the_single_source` | The broker list is defined in exactly one place |

