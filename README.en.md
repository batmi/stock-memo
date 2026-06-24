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
7. [Testing](#7-testing)

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
├── backups.py          # Backup ZIP integrity validation logic (pure functions)
├── templates/          # HTML templates for login & sign-up
├── stock-memo.html     # Frontend main screen structure (HTML)
├── style.css           # Screen design and layout (CSS)
├── calc.js             # Trading calculation single source (same algorithm as stats.py)
├── script.js           # Screen behavior, data communication, chart logic (JavaScript)
├── run.sh              # Automation execution shell script (Mac/Linux)
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

## 7. Testing

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
