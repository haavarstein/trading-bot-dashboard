# Trading Bot - Hermes Cron Job Schedule
# This file documents the cron jobs for the auto-trader

## Schedule Overview:

### Premarket (8:00 AM ET / 12:00 PM UTC)
# Scan for candidates before market open
# Job: premarket-scan

### Market Hours - Autotrader (Every 15 minutes during 9:30 AM - 4:00 PM ET)
# Run the main trading bot
# Job: autotrader-cycle

### Post-Close (4:30 PM ET / 8:30 PM UTC)
# Reconciliation and daily summary
# Job: daily-reconciliation

## Installation Commands:

```bash
# 1. Premarket scan (8:00 AM ET = 12:00 PM UTC)
hermes cronjob create \
  --name "premarket-scan" \
  --schedule "0 12 * * 1-5" \
  --prompt "Scan liquid U.S. stocks for catalysts, volume, and sentiment. Save ranked candidates to ~/trading-bot/data/candidates.json. No trading - analysis only." \
  --workdir "$HOME/trading-bot" \
  --enabled_toolsets terminal,file,web

# 2. Main autotrader (Every 15 min during market hours)
# Runs at :00, :15, :30, :45 from 9:30 AM - 4:00 PM ET (13:30 - 20:00 UTC)
hermes cronjob create \
  --name "autotrader-cycle" \
  --schedule "*/15 13-20 * * 1-5" \
  --script "$HOME/trading-bot/scripts/autotrader.py" \
  --no_agent true \
  --workdir "$HOME/trading-bot" \
  --deliver "origin"

# 3. Daily reconciliation (4:30 PM ET = 8:30 PM UTC)
hermes cronjob create \
  --name "daily-reconciliation" \
  --schedule "30 20 * * 1-5" \
  --prompt "Read ~/trading-bot/data/*.jsonl files. Calculate daily P&L, win rate, consensus rate, and trades blocked. Send summary to Telegram using python3 ~/trading-bot/scripts/telegram_notifier.py" \
  --workdir "$HOME/trading-bot" \
  --enabled_toolsets terminal,file \
  --deliver "origin"
```

## Cron Schedule Explained:

- `*/15 13-20 * * 1-5` = Every 15 minutes, 1:30 PM to 8:00 PM UTC, Mon-Fri
  - Converts to 9:30 AM - 4:00 PM ET (market hours)
  
- `0 12 * * 1-5` = Every day at 12:00 PM UTC (8:00 AM ET), Mon-Fri

- `30 20 * * 1-5` = Every day at 8:30 PM UTC (4:30 PM ET), Mon-Fri

## Notes:

- All jobs run Monday-Friday only (no weekends)
- `no_agent=true` for autotrader means it runs the Python script directly
  - Script stdout is delivered as-is to Telegram
  - No LLM overhead, faster execution
- Premarket and reconciliation use agent mode for reasoning
- `deliver="origin"` sends results back to this chat
