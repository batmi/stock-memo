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

*   Python 3.x or higher
*   Modern web browser (Chrome, Safari, Edge, etc. recommended)
*   (Optional) `ngrok`, `Cloudflare Tunnels`, or `tmux` for external access

---

## 4. Installation & Execution

### Running the Server
Navigate to the project folder and start the local server using the provided script.
If required packages (`Flask`, `Werkzeug`, `waitress`) are missing when the script runs, it will automatically ask to install them.

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
http://127.0.0.1:5000
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
*   **Production Environment (HTTPS)**: While it runs stably on a local network via `waitress`, integrating a reverse proxy (e.g., Nginx) to apply HTTPS (SSL) certificates is highly recommended for proper web publishing.

---

## 6. Project Structure

```text
stock-memo/
├── backend_app.py      # App execution, routes, DB helpers, background threads (Flask)
├── prices.py           # Real-time price inquiry service (modularized by provider + fallback)
├── stats.py            # Trading performance analytics & statistics calculation (pure functions)
├── entry_logic.py      # Trading record saving/integrity validation + INSERT column single source
├── trading_api.py      # System-trading REST API (/api/v1/*) — auth, idempotency, normalization
├── backups.py          # Backup ZIP integrity validation logic (pure functions)
├── templates/          # HTML templates for login & sign-up
├── stock-memo.html     # Frontend main screen structure (HTML)
├── style.css           # Screen design and layout (CSS)
├── calc.js             # Trading calculation single source (same algorithm as stats.py)
├── script.js           # Screen behavior, data communication, chart logic (JavaScript)
├── run.sh              # Automation execution shell script (Mac/Linux)
├── UniversalTradingHistoryAPI.json  # Trading-bot integration API contract (OpenAPI 3.1)
├── backup/             # Daily auto-generated user backup files (ZIP) folder
├── db/                 # Database folder
│   └── journal.db      # Auto-generated trading record database file (SQLite)
├── logs/               # System and error logs folder
│   └── backend_app.log # Debug/Error/Warning server execution log file
└── uploads/            # Attached image files folder
```

> The backend is separated into domain-specific modules (`prices`/`stats`/`entry_logic`/`backups`).
> The profit calculation uses the **exact same moving average cost algorithm** across the frontend (`calc.js`) and backend (`stats.py`), unified to ensure consistency (verified by `tests/calc.test.js`).

---

## 7. Trading API

A REST API that lets an external trading bot (HTS) record its fills on this server in real time.
The full contract is defined in [`UniversalTradingHistoryAPI.json`](UniversalTradingHistoryAPI.json) (OpenAPI 3.1).

### Authentication

1. Issue a key from the web dashboard → Settings → **HTS API key** (it starts with `skm_`).
2. Exchange it for a 24-hour Access Token by calling `POST /api/v1/auth/token` with the key in the `X-API-KEY` header.
3. Send `Authorization: Bearer <token>` on every subsequent request.

> ⚠️ **The key is shown exactly once, right after it is issued.** Only its SHA-256 hash is stored, so it can never be retrieved again — copy it at that moment.
> Revoking a key invalidates tokens issued from it **immediately**.

### Scopes

Keys carry scopes, and tokens inherit them.

| Scope | Allows |
|---|---|
| `trades:write` | Create, amend, and delete trade records; register opening balances |
| `trades:read` | Read trade records and the sync checkpoint |
| `bot:write` | Send bot status pings |

### Main endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/health` | Health check (no auth) |
| `POST` | `/api/v1/auth/token` | Exchange API key for an Access Token |
| `POST` | `/api/v1/trades` | Submit a single trade record |
| `POST` | `/api/v1/trades/batch` | Bulk submit (up to 500, per-item results) |
| `GET` | `/api/v1/trades` | List records for reconciliation (cursor pagination) |
| `GET` | `/api/v1/trades/last-sync` | Last sync checkpoint (for computing the backfill window) |
| `GET` | `/api/v1/trades/by-exec-id/{id}` | Check whether an idempotency key was stored |
| `PATCH` / `DELETE` | `/api/v1/trades/{id}` | Amend / delete a record |
| `POST` | `/api/v1/positions/opening` | Register holdings held before the integration started |
| `POST` | `/api/v1/bot/status` | Bot liveness ping |

### Design principles

*   **Idempotency**: `brokerExecutionId` carries a UNIQUE constraint, so a duplicate resend creates nothing new and returns the existing record with `200`. A bot that never received a response can therefore **always resend safely**.
    The recommended key format is `{env}:{account}:{fill date}:{order no}` — broker order numbers are reused every business day, so the order number alone must never be used.
*   **No silent loss**: A fill sent by a bot is **stored and flagged `needsReview`** even when it violates integrity checks (e.g. selling more than the recorded holding). Returning `400` would make every retry fail identically and the fill would vanish for good. (Records typed by a human in the web UI are still blocked, since the user can fix them on the spot.)
*   **Mock vs. real separation**: The `isSimulated` flag stores them separately and excludes them from default queries and statistics.
*   **System vs. discretionary separation**: The `isSystem` flag classifies only automated orders as `시스템` (system). A bot reports **every** fill on its accounts — including orders placed by hand in a mobile app or broker HTS — so without this distinction automated performance and discretionary trading end up in one bucket. → [Trade class](#trade-class--what-counts-as-system)
*   **Trading-day attribution**: `executedAt` is accepted as RFC3339 with an offset and, together with `exchange`, yields the **exchange-local trading day** (`tradeDate`). A US after-hours fill is no longer pushed onto the next Korean date.
*   **Rate limiting**: Token issuance is 10 requests per 5 minutes per IP (brute-force protection); other API calls are 600 per minute per key. Exceeding either returns `429` with `Retry-After`.

### Trade class — what counts as "system"

A bot reports **every** fill on its accounts. Orders placed by hand in the Toss app or a broker's own HTS are detected by balance reconciliation and uploaded too. The server used to fill an empty `tradeClass` with `시스템` (system), which lumped all of them into automated performance.

The bot now sends `isSystem` to tell them apart.

| `isSystem` | Class assigned by the server |
|---|---|
| `true` | Pinned to `시스템` (system) |
| `false` | Inherited from the latest record for the same symbol → empty if there is none |
| *(field absent)* | Same as above (the bot does not know the origin) |

If the bot sends an explicit `tradeClass`, that value is used as-is.

**Inheritance rule**: the class is taken from the most recent record for the same user and symbol, but **`시스템` is never inherited.** An earlier version stored every bot record as `시스템`, so inheriting it would make the very contamination this fixes permanent. Inheritance only picks up classes a human actually chose (`장기투자` / long-term, `배당투자` / dividend, …).

Opening balances (`POST /api/v1/positions/opening`) are `isSystem=false` as well — they were held before the integration started and were not filled by the bot.

> Records already stored as `시스템` stay as they are. Idempotency means even a re-sync will not overwrite them, so fix those on the web.

### Running several bots (botId)

Heartbeats and commands are scoped **by user, not by API key** — the key is exchanged for a username right after authentication and which key it was is then forgotten. Issuing separate keys therefore does not separate several HTS instances.

`botId` (in the ping body) is that separator. On the HTS side it comes from the `JOURNAL_BOT_ID` environment variable.

*   **Status**: recorded per instance in the `bots` table. The headline indicator follows the **worst** bot — "green if any bot is alive" would let a mock bot's ping mask a dead live bot. Which bot it is shows in the list underneath the indicator.
*   **Commands**: a re-sync is queued against a specific bot. If two or more bots are connected and no target is given, the server rejects the request with `400` — commands are delivered at-most-once, so whichever bot pings first would take it, ack it, and the screen would read "done" while nothing was recovered (a **silent failure**).
*   **Backward compatibility**: bots that send no `botId` are grouped under `default`. An untargeted command is delivered only when exactly one bot is registered.

### Re-sync — restoring deleted records

When you delete a record on the web, **the bot does not automatically send it again.** The bot only remembers *that it sent* a record, never whether it is still there — and that is the correct behaviour: if deliberate deletions kept coming back, there would be no way to delete anything.

To restore them, use **Settings → Account Settings → 재동기화 (Re-Sync)** and pick a range: last quarter (90d), half-year (180d), or year (365d). All presets are **rolling**, not calendar-based — pressing "quarter" at the start of a calendar quarter would otherwise cover only a few days and miss the gap entirely.

> The API (`POST /api/me/bot/resync`) also accepts an explicit `from`/`to`. The UI omits those inputs because the presets suffice, not because the bot cannot handle arbitrary ranges.

**No duplicates are created.** Idempotency on `brokerExecutionId` makes existing records come back as `duplicate`. Re-syncing 90 days when only 10 days were actually deleted returns `inserted=10, skipped=80`. Those two numbers *are* the answer to "what was missing, and how much?", so the web shows them separately. Since duplicates are free, err on the side of a longer range.

#### How commands reach the bot

The bot usually sits behind a home network, so **the server can never initiate a connection.** Pressing the button queues a row in `bot_commands`, which rides along on the bot's next ping response (≤10s).

```
POST /api/v1/bot/status  →  { "command": "resync", "commandId": 17,
                              "commandParams": { "from": "2026-05-04", "to": null } }
next ping request body   ←  { "status": "running",
                              "commandAck": { "id": 17, "result": "queued", "count": 42 } }
```

*   **A command is delivered at most once.** It is not re-sent even if the ack never arrives.
    Re-sending until acked would guarantee execution, but if the bot restarts after receiving the command and before acking, **the same re-sync runs a second time.** That is idempotent with respect to server data yet **not idempotent with respect to operator intent** — records deleted on purpose between the two runs would come back. Making the operator press the button again is by far the lesser evil.
*   **A command shown as "완료" (done) is never sent again, under any circumstance.** The bot's duplicate-execution guard lives only in memory and is lost on restart, so this guarantee is the server's job.
*   If the bot never picks it up, the command expires after an hour and shows as "미처리" (unhandled) — usually because the bot was switched off. If it was picked up but never reported back, it stays "처리 중" (running) until then.
*   Pressing the button repeatedly does not queue duplicates while one is still pending.
*   A bot reporting `status=stopped` is given no work; it could not act on it anyway.
*   A malformed ack still returns `200` for the ping itself. Returning `400` would break the heartbeat and flip the display to "disconnected".

#### After restoring a backup, run a re-sync

The backup ZIP contains `data.json` (trade records), attached images, and `account_info.json` (account mappings). **The `api_keys` and `users` tables are not included** — a backup restores data into an existing account; it does not stand up an empty server from scratch. If you restore onto a new server, issue a fresh API key on the web and update `JOURNAL_API_KEY` on the HTS side.

Also, **records the bot sent after the backup point do not come back with the restore.** The bot only remembers *that it sent* them, so it never re-sends on its own, and backfill cannot catch this gap either (those rows are still marked as sent in the bot's queue). **Only a re-sync fills it in.** There is exactly one place to request it — the **Re-sync** button under Settings → Account settings — so that picking a period and watching progress stays a single flow; the restore screen does not offer it.

Because re-sync reads the bot's local trade history as the source of truth, it also recovers records old enough to have been pruned from the bot's own queue.

> ⚠️ **`pause`/`resume` are deliberately unimplemented.** They exist in the API spec enum but are excluded from `SUPPORTED_BOT_COMMANDS`, so the server refuses to even queue them. Re-sync means "re-send data that is already the bot's"; `pause` means **the web server can halt a trading bot**. A compromised or buggy web layer could stop the bot while it holds a position. If ever needed, it requires its own design with confirmation and auto-expiry safeguards — it must not be treated like re-sync.

### Error responses

```json
{ "error": "human-readable message", "errorCode": "OVERSELL", "details": {} }
```

Always branch on `errorCode`. The `error` wording may change without notice.

### Operational notes

*   **Serve over HTTPS** so the API key is never transmitted in the clear (reverse proxy such as Nginx + SSL).
*   Rate limiting is in-process memory. Scaling to multiple processes requires swapping it for a shared store such as Redis.

---

## 8. Testing

Backend APIs, data integrity validations, backup restorations (round-trip), and performance analytics logic are verified by `pytest`-based unit tests.
Test codes are located in the `tests/` folder and use a temporary DB, ensuring the actual operational data (`db/journal.db`) remains unaffected.

```bash
# Run all backend tests
pytest

# Run in concise output mode
pytest -q
```

The frontend calculation engine (`calc.js`) is verified by the built-in Node test runner. It uses the exact same fixtures as the backend statistical tests to guarantee consistent results between the front and back ends.

```bash
# Run frontend calculation unit tests (Node 18+)
node --test tests/calc.test.js
```
