# 🤖 IBKR Auto-Trader with Telegram Notifications

**Status:** DRY-RUN MODE (Safe to test)  
**Capital:** $1,000  
**Max Positions:** 3 (PDT-safe)  
**Notifications:** Telegram (@your_telegram_bot)

---

## 🎯 What This Bot Does

1. **Scans 5 stocks** every 15 minutes during market hours (TSLA, NVDA, AAPL, SPY, QQQ)
2. **Dual-model consensus**: Two AI models must agree on every trade
3. **Risk validation**: Code enforces stop-loss, position sizing, R:R ratios
4. **Telegram alerts**: Real-time notifications for trades, disagreements, blockers
5. **Full logging**: Every decision, consensus, and order recorded in JSONL

---

## 🏗️ Architecture (Farzad.money Pattern)

```
┌─────────────────────────────────────────┐
│ LAYER 1: DUAL-MODEL CONSENSUS          │
│ • Grok Beta + Claude Sonnet 4.5        │
│ • Independent decisions (no collusion)  │
│ • Must agree on action AND symbol      │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ LAYER 2: DETERMINISTIC VALIDATION      │
│ • Buying power check                    │
│ • Position size limits                  │
│ • Risk/reward ratio (min 1.5:1)        │
│ • Stop-loss requirements                │
│ • Symbol whitelist                      │
│ • Kill switch check                     │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ LAYER 3: EXECUTION (DRY-RUN)           │
│ • Simulated order placement             │
│ • Telegram notification                 │
│ • Order ledger logging                  │
│ • Journal entry                         │
└─────────────────────────────────────────┘
```

---

## 📁 File Structure

```
~/trading-bot/
├── config/
│   └── autonomy_config.json       ← All trading rules & limits
├── scripts/
│   ├── telegram_notifier.py       ← Telegram integration
│   └── autotrader.py              ← Main trading logic
├── data/
│   ├── trade_journal.jsonl        ← All trades
│   ├── order_ledger.jsonl         ← All orders (proposed/rejected/filled)
│   ├── consensus_log.jsonl        ← Model agreements/disagreements
│   └── candidates.json            ← Premarket scan results
├── logs/                          ← Cron job logs (auto-created)
├── CRON_SCHEDULE.md              ← Hermes cron job setup
├── KILL_SWITCH_README.md         ← Emergency stop instructions
└── README.md                      ← This file
```

---

## 🚀 Quick Start

### 1. Test Telegram Notifications

```bash
cd ~/trading-bot
python3 scripts/telegram_notifier.py
```

You should receive a test message on Telegram.

### 2. Run a Dry-Run Cycle Manually

```bash
python3 scripts/autotrader.py
```

Watch for:
- ✅ Consensus reached between models
- ✅ Validation passed
- 🧪 DRY-RUN trade notification to Telegram

### 3. Install Hermes Cron Jobs

See `CRON_SCHEDULE.md` for the three cron job commands:

```bash
# Premarket scan (8:00 AM ET daily)
hermes cronjob create --name "premarket-scan" --schedule "0 12 * * 1-5" ...

# Autotrader (every 15 min during market hours)
hermes cronjob create --name "autotrader-cycle" --schedule "*/15 13-20 * * 1-5" ...

# Daily reconciliation (4:30 PM ET daily)
hermes cronjob create --name "daily-reconciliation" --schedule "30 20 * * 1-5" ...
```

### 4. Monitor

```bash
# View consensus log
tail -f ~/trading-bot/data/consensus_log.jsonl

# View order ledger
tail -f ~/trading-bot/data/order_ledger.jsonl

# List cron jobs
hermes cronjob list
```

---

## 🛡️ Safety Features

### Hard Limits (autonomy_config.json)
- ✅ Max $200 per position
- ✅ Max 3 open positions
- ✅ Max 3 daily orders (PDT-safe)
- ✅ 2% max loss per trade
- ✅ 5% max daily drawdown
- ✅ Min 1.5:1 risk/reward ratio

### Banned Instruments
- ❌ Options
- ❌ Crypto
- ❌ Margin
- ❌ Penny stocks (<$5)
- ❌ Illiquid stocks (<500k avg volume)
- ❌ Leveraged/Inverse ETFs

### Kill Switch
Create this file to stop all trading:
```bash
touch ~/trading-bot/KILL_SWITCH.txt
```

---

## 📊 Telegram Notifications

You'll receive notifications for:

1. **🟢 Trade Signals** (dry-run or live)
   - Action, symbol, price, qty
   - Stop loss, take profit, R:R ratio
   - Confidence score, thesis

2. **⚠️ Model Disagreements**
   - Shows both model decisions
   - Reason for blocking

3. **🛑 Validation Blockers**
   - Insufficient buying power
   - Position size violations
   - Risk/reward too low
   - Kill switch active

4. **❌ System Errors**
   - Auth failures
   - Script crashes
   - API errors

5. **📈 Daily Summary** (post-close)
   - Trades executed
   - Win rate
   - P&L
   - Consensus rate

---

## 🔄 Transition to LIVE Mode

### Prerequisites:
1. ✅ Run dry-run for at least 2 weeks
2. ✅ Verify Telegram notifications work
3. ✅ Review all consensus logs
4. ✅ Set up IBKR API access (TWS or IB Gateway)
5. ✅ Install IBKR MCP server
6. ✅ Test with paper trading account first

### To Enable LIVE:
1. Edit `config/autonomy_config.json`:
   ```json
   {
     "mode": "LIVE",
     "enabled": true
   }
   ```

2. Update `autotrader.py` to use real IBKR MCP calls instead of mock data

3. **START WITH TINY CAPITAL** (e.g., $100)

4. Monitor closely for first week

---

## 📝 Daily Workflow

**Morning (before market):**
- Check Telegram for premarket scan results
- Review kill switch status
- Verify cron jobs are running

**During market hours:**
- Monitor Telegram for trade signals
- Watch for disagreement notifications
- Check validation blockers

**After close:**
- Review daily summary
- Check consensus log for patterns
- Update strategy if needed

---

## 🚨 Troubleshooting

**No Telegram notifications?**
```bash
# Check bot token in Hermes gateway config
cat ~/.hermes/gateway.yaml | grep telegram

# Test manually
python3 ~/trading-bot/scripts/telegram_notifier.py
```

**Cron jobs not running?**
```bash
# List jobs
hermes cronjob list

# Check logs
hermes cronjob logs autotrader-cycle
```

**Bot keeps blocking trades?**
```bash
# Check consensus log for reasons
cat ~/trading-bot/data/consensus_log.jsonl | jq '.reason'

# Common issues:
# - Models disagree on symbol
# - Confidence < 70%
# - Stop/target mismatch
# - Buying power insufficient
```

---

## 📚 Key Files

- **autonomy_config.json**: All trading rules (edit this to change limits)
- **telegram_notifier.py**: Telegram integration (works standalone)
- **autotrader.py**: Main bot logic (currently uses mock data)
- **consensus_log.jsonl**: Every model decision + outcome
- **order_ledger.jsonl**: Every proposed/executed order

---

## ⚡ Next Steps

1. **Install IBKR MCP server** (for live broker data)
2. **Connect TradingView MCP** (for real-time market data)
3. **Run dry-run for 2 weeks** (build confidence)
4. **Review disagreement patterns** (improve consensus)
5. **Transition to paper trading** (IBKR paper account)
6. **Start live with $100** (test real execution)

---

**Built with:** Hermes Agent + Farzad.money pattern  
**License:** Your own risk - this is experimental  
**Support:** Review logs, not financial advice

---

## 🎓 Learning Resources

- Farzad.money instructions: https://farzad.money/instructions.html
- Miles Deutscher TradingView MCP: https://x.com/milesdeutscher/status/2084655895926255662
- IBKR API docs: https://interactivebrokers.github.io/
- Hermes docs: https://hermes-agent.nousresearch.com/docs
