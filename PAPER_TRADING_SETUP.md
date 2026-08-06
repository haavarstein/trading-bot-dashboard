# 🎯 PAPER TRADING SETUP - STEP BY STEP

**Status:** READY FOR PAPER TRADING  
**Capital:** $1,000 (IBKR Paper Account)  
**Mode:** Real APIs, No Real Money  
**Stock Selection:** AI decides (no hardcoded limit)

---

## ✅ WHAT CHANGED:

### 1. Configuration Updated
- Mode: `DRY_RUN` → `PAPER_TRADING`
- Enabled: `false` → `true`
- Stocks: Fixed 5 → `AI_DECIDES` (Alpha Radar scans market)

### 2. Alpha Radar Created
- **File:** `scripts/alpha_radar.py`
- **Function:** Scans market for liquid stocks with catalysts
- **Output:** `data/candidates.json`

### 3. IBKR MCP Configured
- **Server:** `interactive-brokers-mcp`
- **Status:** Configured (disabled until TWS/Gateway running)
- **Account Type:** Paper

### 4. TradingView MCP
- **Status:** Already installed & working
- **Will provide:** Real-time quotes, RSI, MACD, EMAs, OHLCV

---

## 📋 PREREQUISITES CHECKLIST:

Before you can paper trade, you need:

### 1. IBKR Paper Account Setup
- [ ] Go to https://www.interactivebrokers.com/
- [ ] Sign up for paper trading account (free)
- [ ] Get your paper account ID (starts with "DU")
- [ ] Download TWS (Trader Workstation) or IB Gateway

### 2. TWS/Gateway Installation
- [ ] Install TWS or IB Gateway
- [ ] Configure API settings:
  - Enable ActiveX and Socket Clients
  - Socket Port: 7497 (paper) or 4001 (live)
  - Read-Only API: NO (we need to place orders)
  - Master API Client ID: Leave blank
- [ ] Accept incoming connection from localhost

### 3. Configuration Updates
```bash
# Edit ~/trading-bot/config/autonomy_config.json
# Replace "DU######" with your REAL paper account ID
```

---

## 🚀 INSTALLATION STEPS:

### Step 1: Run Alpha Radar Test
```bash
cd ~/trading-bot
python3 scripts/alpha_radar.py
```

**Expected output:**
```
Alpha Radar Scan - 2026-08-06 12:00:00 UTC
Found 5 qualified candidates:
  • TSLA: $321.55 | Vol: 27,820,813 | EV delivery numbers expected
  • NVDA: $125.50 | Vol: 45,000,000 | AI chip demand strong
  ...
✓ Saved 5 candidates to ./data/candidates.json
```

### Step 2: Start TWS/IB Gateway
```
1. Launch TWS or IB Gateway
2. Login with your paper account credentials
3. Go to Edit → Global Configuration → API → Settings
4. Enable "ActiveX and Socket Clients"
5. Set Socket Port to 7497 (for paper)
6. Uncheck "Read-Only API"
7. Click "OK" and restart TWS/Gateway
```

### Step 3: Test IBKR MCP Connection
```bash
# Enable IBKR MCP
hermes config set mcp_servers.ibkr.enabled true

# Restart Hermes (required for MCP to connect)
# Close this session and start a new one

# Test connection
hermes mcp test ibkr
```

**Expected:** `✓ ibkr connected` with list of available tools

### Step 4: Verify TradingView MCP
```bash
hermes mcp list | grep tradingview
```

**Expected:** `tradingview ... ✓ enabled` (84 tools)

---

## 🔧 UPDATE AUTOTRADER FOR REAL APIs:

The current `autotrader.py` uses mock data. You need to replace:

### Replace Mock Broker Snapshot:
```python
# OLD (line ~70):
def get_mock_broker_snapshot(self) -> Dict:
    return {"buying_power": 950.00, ...}

# NEW:
def get_real_broker_snapshot(self) -> Dict:
    # Call IBKR MCP
    result = subprocess.run([
        'hermes', 'mcp', 'call', 'ibkr', 
        'get_account', '{}'
    ], capture_output=True, text=True)
    
    account = json.loads(result.stdout)
    
    return {
        "account_id": account['accountId'],
        "buying_power": account['buyingPower'],
        "positions": account['positions'],
        "pending_orders": account['pendingOrders']
    }
```

### Replace Mock Market Data:
```python
# OLD (line ~80):
def get_mock_market_data(self, symbol: str) -> Dict:
    mock_data = {"TSLA": {"price": 321.55, ...}}
    return mock_data.get(symbol)

# NEW:
def get_real_market_data(self, symbol: str) -> Dict:
    # Use TradingView MCP (already working!)
    subprocess.run([
        'hermes', 'mcp', 'call', 'tradingview',
        'chart_set_symbol', f'{{"symbol": "{symbol}"}}'
    ])
    
    # Get quote
    quote_result = subprocess.run([
        'hermes', 'mcp', 'call', 'tradingview',
        'quote_get', '{}'
    ], capture_output=True, text=True)
    quote = json.loads(quote_result.stdout)
    
    # Get indicators
    indicators_result = subprocess.run([
        'hermes', 'mcp', 'call', 'tradingview',
        'data_get_study_values', '{}'
    ], capture_output=True, text=True)
    indicators = json.loads(indicators_result.stdout)
    
    return {
        "price": quote['last'],
        "volume": quote['volume'],
        "rsi": indicators['studies'][0]['values']['RSI'],
        # Parse other indicators...
    }
```

---

## 📊 FARZAD PATTERN IMPLEMENTATION:

### Current State:
```
✓ Alpha Radar (candidate scanning)
✓ Dual-model consensus (Grok + Claude)
✓ Deterministic validation
✓ Telegram notifications
✓ Full logging (JSONL)
✓ Kill switch

⏳ IBKR MCP (waiting for TWS)
⏳ TradingView real data (need to update autotrader.py)
```

### What You'll Get:
1. **Premarket:** Alpha Radar scans → `candidates.json`
2. **Market Hours:** Autotrader every 15 min:
   - Reads IBKR account (real buying power, positions)
   - Gets TradingView data (real prices, RSI, MACD)
   - Both AI models analyze independently
   - If consensus + validation → place paper order via IBKR
   - Telegram notification of trade
3. **Post-Close:** Reconciliation, P&L calculation, daily summary

---

## ⚠️ BEFORE YOU RUN LIVE:

1. **Test Alpha Radar first:**
   ```bash
   python3 scripts/alpha_radar.py
   ```

2. **Verify TWS is running and API enabled**

3. **Test IBKR MCP manually:**
   ```bash
   hermes mcp call ibkr get_account '{}'
   ```

4. **Test TradingView MCP:**
   ```bash
   hermes mcp call tradingview quote_get '{}'
   ```

5. **Update autotrader.py** to use real APIs (or I can do it for you)

6. **Run ONE manual cycle:**
   ```bash
   python3 scripts/autotrader.py
   ```

7. **Check Telegram for notification**

8. **If all passes → install cron jobs**

---

## 🎯 NEXT IMMEDIATE STEP:

**What do you want to do first?**

A. **I'll set up IBKR paper account** (need instructions)  
B. **I already have IBKR** (give me TWS/Gateway setup steps)  
C. **Update autotrader.py to use real APIs** (you do it)  
D. **Test Alpha Radar and show me candidates** (verify scanning works)  

---

**Current Status:** Configuration ready, waiting for:
1. IBKR paper account setup
2. TWS/Gateway running
3. Autotrader updated for real APIs

Tell me which step you want to tackle first!
