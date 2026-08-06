# 🤖 TRADING BOT - QUICK REFERENCE

## 🚀 Daily Commands

### Check Bot Status
```bash
cd ~/trading-bot
python3 scripts/autotrader.py          # Run one cycle manually
hermes cronjob list                     # View all scheduled jobs
```

### Monitor Logs
```bash
# View recent decisions
tail -20 ~/trading-bot/data/consensus_log.jsonl | jq

# View executed orders
tail -20 ~/trading-bot/data/order_ledger.jsonl | jq

# Count disagreements today
grep "consensus.*false" ~/trading-bot/data/consensus_log.jsonl | wc -l
```

### Control Trading
```bash
# STOP trading immediately
touch ~/trading-bot/KILL_SWITCH.txt

# RESUME trading
rm ~/trading-bot/KILL_SWITCH.txt

# Pause cron jobs
hermes cronjob pause autotrader-cycle

# Resume cron jobs
hermes cronjob resume autotrader-cycle
```

---

## 📊 Current Settings

**Capital:** $1,000  
**Max Position:** $200  
**Max Positions:** 3  
**Stocks:** TSLA, NVDA, AAPL, SPY, QQQ  
**Min R:R:** 1.5:1  
**Max Loss/Trade:** 2%  

**Mode:** DRY_RUN (safe)  
**Telegram:** Enabled (recipient configured locally)

---

## ⚙️ Edit Trading Rules

```bash
# Open config
nano ~/trading-bot/config/autonomy_config.json

# Example changes:
# - Add more symbols to "allowed_symbols"
# - Change "max_position_size_usd"
# - Adjust "min_risk_reward_ratio"
# - Update "max_daily_orders"
```

---

## 🔔 Telegram Notifications

You receive alerts for:
- 🟢 Trade signals (BUY/SELL with details)
- ⚠️ Model disagreements (what each model said)
- 🛑 Validation blockers (why trade was blocked)
- ❌ System errors (auth failures, crashes)
- 📈 Daily summary (P&L, win rate, consensus rate)

Test:
```bash
python3 ~/trading-bot/scripts/telegram_notifier.py
```

---

## 📅 Cron Schedule

| Time (ET) | Job | Frequency |
|-----------|-----|-----------|
| 8:00 AM | Premarket scan | Daily (Mon-Fri) |
| 9:30-4:00 PM | Autotrader | Every 15 minutes |
| 4:30 PM | Daily reconciliation | Daily (Mon-Fri) |

---

## 🛡️ Safety Features

✅ Dual-model consensus (both must agree)  
✅ Deterministic validation (code checks all rules)  
✅ Kill switch (instant stop)  
✅ Position limits (max $200 per stock)  
✅ Stop-loss required (every trade)  
✅ Risk/reward minimum (1.5:1)  
✅ Banned instruments (options, crypto, margin)  
✅ Full logging (every decision recorded)

---

## 🚨 Emergency

**Bot going crazy?**
```bash
# STOP EVERYTHING NOW
touch ~/trading-bot/KILL_SWITCH.txt
hermes cronjob pause autotrader-cycle

# Then investigate
cat ~/trading-bot/data/consensus_log.jsonl | tail -50 | jq
```

**Lost money?**
```bash
# Review what happened
cat ~/trading-bot/data/order_ledger.jsonl | jq '.symbol, .action, .entry_price, .stop_loss'

# Check if stop-loss hit
# (manually in IBKR TWS - bot doesn't manage exits yet)
```

---

## 📈 Going LIVE Checklist

Before enabling live mode:

- [ ] Ran dry-run for 2+ weeks
- [ ] IBKR API connected & tested
- [ ] Paper trading successful
- [ ] Telegram working
- [ ] Kill switch tested
- [ ] Reviewed ALL consensus logs
- [ ] Understand PDT rule
- [ ] Start with $100 only

Then edit:
```json
{
  "mode": "LIVE",
  "enabled": true,
  "account": {
    "starting_capital": 100  // START TINY!
  }
}
```

---

## 🔗 Important Files

| File | Purpose |
|------|---------|
| `autonomy_config.json` | All trading rules |
| `autotrader.py` | Main bot logic |
| `telegram_notifier.py` | Notifications |
| `consensus_log.jsonl` | Model decisions |
| `order_ledger.jsonl` | All orders |
| `KILL_SWITCH.txt` | Create = stop trading |

---

**Quick Help:**
```bash
cat ~/trading-bot/README.md              # Full docs
cat ~/trading-bot/SETUP_COMPLETE.md      # Setup verification
cat ~/trading-bot/KILL_SWITCH_README.md  # Emergency stop
```
