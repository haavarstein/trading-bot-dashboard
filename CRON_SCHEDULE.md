# Trading Bot - Hermes Cron Job Schedule

> Live source of truth is README.md → **Automation (Hermes cron)** and
> `hermes cronjob list`. This file is a quick reference for the current live jobs.

## Live jobs

### nyse-paper-session-15m
- **Runner:** `scripts/run_paper_session.py` (scan + desk + paper fills + dashboard push)
- **Schedule:** `*/15 10-17 * * 1-5` (America/New_York window)
- **Session gate:** enforced in the runner code (`09:30–16:00 America/New_York`),
  not by the cron text — off-hours ticks print `SKIP` and open no risk.

### nyse-mark-publish-5m
- **Runner:** `scripts/mark_and_publish.py` (IBKR holdings/SPY marks + dashboard push)
- **Schedule:** `*/10 9-15 * * 1-5` (America/New_York window)
- **Quotes:** IBKR official gateway (delayed) primary, yfinance fallback.

### nyse-open-close-notify
- **Runner:** NYSE open/close Telegram pings (computed in local time).

### trading-weekly-readiness
- **Runner:** `scripts/weekly_readiness.py` — go-live readiness report to Telegram.
- **Schedule:** Saturday 11:00 (America/New_York window).

## Notes

- Live jobs are managed with `hermes cronjob list` / `hermes cronjob ...`.
- Telegram delivers fill alerts + credit alerts + weekly readiness only — no
  per-session holdings digest.
- Cron commits only public snapshot files (`dashboard-data.json`, `index.html`)
  to `main`; strategy/code lives on branches.
