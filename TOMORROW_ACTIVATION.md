# 🚀 TOMORROW: ACTIVATION CHECKLIST

**Your IBKR paper trading account will be active!**

---

## 📋 STEP-BY-STEP ACTIVATION:

### 1. Check Email for Account Activation (Morning)
- Look for email from Interactive Brokers
- It will contain your **Account ID** (format: DU######)
- Copy that ID - we need it!

### 2. Download & Install TWS or IB Gateway
**Option A: IB Gateway (Recommended - lighter)**
- Download: https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
- Install for Windows
- Lighter than full TWS
- API-focused

**Option B: Trader Workstation (TWS)**
- Download: https://www.interactivebrokers.com/en/trading/tws.php
- Full trading platform
- More features but heavier

### 3. First Login
```
1. Launch IB Gateway (or TWS)
2. Select "IB API" mode
3. Username: <IBKR_USERNAME>
4. Password: (you have it)
5. Trading Mode: PAPER TRADING
6. Click Login
```

### 4. Enable API Access
```
1. Go to: Edit → Global Configuration → API → Settings
2. Enable "ActiveX and Socket Clients" ✓
3. Set "Socket port": 7497 (for paper trading)
4. Uncheck "Read-Only API" ✗
5. Set "Master API client ID": (leave blank)
6. Click "OK"
7. Restart IB Gateway/TWS
```

### 5. Update Trading Bot Config
```bash
# Edit .env file
nano ~/trading-bot/.env

# Update this line with your real account ID:
IBKR_ACCOUNT_ID=DU######  # Replace with your actual ID from email
```

### 6. Enable IBKR MCP Server
```bash
# Enable the MCP server
hermes config set mcp_servers.ibkr.enabled true

# Restart Hermes (required)
# Close this session, start a new one
```

### 7. Test IBKR Connection
```bash
# After Hermes restart:
hermes mcp test ibkr

# Should see:
# ✓ ibkr connected
# Tools available: get_account, get_positions, place_order, ...
```

### 8. Test Real API Calls
```bash
# Get your paper account info
hermes mcp call ibkr get_account '{}'

# Should return:
# {
#   "accountId": "DU######",
#   "buyingPower": 1000000.00,  # IBKR gives $1M in paper
#   "positions": [],
#   "cash": 1000000.00
# }
```

### 9. Update Autotrader for Real APIs
**I'll do this part - just ping me when steps 1-8 are done!**

We need to replace:
- `get_mock_broker_snapshot()` → real IBKR MCP calls
- `get_mock_market_data()` → real TradingView MCP calls
- `get_model_decision()` → real AI model calls (Grok + Claude)

### 10. Run First Paper Trade!
```bash
cd ~/trading-bot

# Run one cycle manually
python3 scripts/autotrader.py

# Watch for:
# - Real IBKR buying power
# - Real TradingView prices
# - Real consensus
# - Real order placement (paper)
# - Telegram notification
```

---

## 🔐 SECURITY REMINDERS:

1. **Credentials stored in:** `~/trading-bot/.env` (gitignored)
2. **After testing works:** Change your IBKR password
3. **Never commit .env to git**
4. **This is paper money** - safe to test aggressively

---

## ⚡ QUICK START (Tomorrow Morning):

```bash
# 1. Get account ID from email
# 2. Launch IB Gateway, login
# 3. Enable API (port 7497)
# 4. Update .env with account ID
# 5. Tell me: "IBKR is ready"
# 6. I'll update autotrader.py for real APIs
# 7. Test first trade!
```

---

## 📞 WHEN YOU'RE READY TOMORROW:

Just send me:
> "IBKR activated, account ID is DU######"

And I'll:
1. Update the config
2. Connect real IBKR MCP
3. Connect real TradingView MCP  
4. Test full paper trading cycle
5. Install cron jobs
6. You'll be live paper trading!

---

## 🎯 WHAT YOU'LL GET:

**Automated Paper Trading:**
- Alpha Radar scans every 15 minutes
- AI picks best stocks from scan
- Dual models must agree
- Places real paper orders via IBKR
- Telegram notifications for every trade
- Full logging for review
- $1,000,000 paper money to test with (IBKR standard)

**Daily Workflow:**
- 8:00 AM: Premarket scan
- 9:30-4:00 PM: Trade every 15 min (if setups found)
- 4:30 PM: Daily summary to Telegram

---

## 💡 TIPS:

- **Take your time with API setup** - most important step
- **Test with ONE manual cycle first** before cron jobs
- **Watch first few trades closely** via Telegram
- **Check IBKR Activity panel** to see paper orders
- **Have fun!** This is paper money, experiment freely

---

**Files prepared:**
- ✅ `.env` (credentials secured)
- ✅ `.gitignore` (protecting secrets)
- ✅ `autonomy_config.json` (ready for account ID)
- ✅ Alpha Radar (working)
- ✅ Autotrader (ready for API update)

**See you tomorrow! 🚀**
