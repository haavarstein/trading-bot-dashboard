# Hermes Auto-Trader (Simulated Paper)

Public monitor + local simulated paper trading bot inspired by the Farzad.money trust-dashboard pattern.

**Live dashboard:** https://trading-bot-delta-roan.vercel.app  
**Mode today:** `PAPER_TRADING` via **local paper broker** (real market marks, simulated cash/fills)  
**IBKR MCP:** paused until a personal paper account is approved/active  
**Capital:** $1,000 starting simulated cash  
**Cadence:** every 15 minutes during NYSE regular hours (09:30–16:00 America/New_York, Mon–Fri)

> This is experimental software, not financial advice. Early paper results are not proof of edge.

---

## What it does

1. **Alpha Radar** ranks liquid equity candidates from catalyst/news + liquidity signals (`scripts/alpha_radar.py` → `data/candidates.json`).
2. **Decision engine** (`scripts/autotrader.py`) chooses BUY / SELL / HOLD from ranked candidates + open portfolio state.
3. **Market data** via Financial Modeling Prep (primary) with yfinance fallback (`scripts/market_data.py`).
4. **Local paper broker** (`scripts/paper_broker.py`) is account truth: cash, positions, fills, realized/open P/L, SPY benchmark start.
4. **Dashboard publisher** rebuilds `dashboard-data.json` and pushes the public snapshot to GitHub → Vercel.
5. **Telegram session report** summarizes decision/fill status plus per-holding `+/- $` and `%` bullets (mobile-friendly).

There is **no live broker order routing** in the current path.

---

## Architecture (current)

```
Alpha Radar (news/catalyst rank)
        ↓
Dual decision paths must agree on action + symbol
        ↓
Deterministic validation (cash, size, stop/target, R:R, concentration)
        ↓
Local Paper Broker fills (BUY/SELL)  ← account source of truth
        ↓
JSONL ledgers + portfolio.json
        ↓
generate_dashboard_data.py → dashboard-data.json → Vercel
        ↓
Telegram alerts on **BUY/SELL fills only** (no HOLD spam) + dashboard link
```

### Consensus models (config) — junior first, senior gate
**Juniors (cheap screen, every cycle) — 4 independent models**
- `junior_model_1`: `grok-4.3` (fallback `grok-build-0.1`) via xAI
- `junior_model_2`: `claude-haiku-4-5` via Anthropic
- `junior_model_3`: `deepseek-v4-flash` via Nous Portal (`NOUS_API_KEY`)
- `junior_model_4`: `grok-build-0.1` via xAI
- `junior_min_agree`: 3 (3-of-4 majority HOLD finalizes without seniors)

**Seniors (final gate when escalated)**
- `model_1`: `grok-4.5` (`model_1_effort`: `medium`) via xAI
- `model_2`: `claude-sonnet-5` via Anthropic

**Escalation rules**
- Juniors always see the same ranked evidence (max 8 candidates)
- Escalate to seniors on **any live BUY/SELL intent**, split, or when junior quorum is missed
- 3-of-4 majority HOLD finalizes cheaply without seniors
- Agreed junior **HOLD** can finalize without seniors (saves tokens)
- Senior dual agreement remains the final trade gate; senior disagreement blocks execution
- `min_confidence`: 70 (BUY/SELL); junior HOLD floor 55
- Dual agreement required on **BUY/SELL** (same symbol); pure **HOLD+HOLD agrees even if watch symbols differ**
- Rotation SELLs allowed when book is full; stop/target still primary exits
- Missing keys / failed calls → tagged deterministic `source=fallback`

### Honest status of dual models
Junior→senior path is implemented in `scripts/dual_llm.py` (`run_junior_senior_consensus`) and wired in `autotrader.py`.
Keys live only in local gitignored `.env` — never in git.
Dashboard reports `decision_mode`, junior model names, and last `tier` / escalate reason.

---

## Hard limits (config)

From `config/autonomy_config.json` (source of truth):

| Limit | Typical value |
|------|----------------|
| Max single position | $200 |
| Max open positions | 5 (capital-sized: ~$1000 / $200) |
| Min new-buy reward-to-risk | 1.5:1 |
| Daily loss halt | 3% (~$30 on $1k start) |
| Portfolio drawdown halt | 8% |
| Stop + target | required on entries |
| Rotation | allowed with min-hold gate (default 45m) |
| Session | NYSE regular hours only |

**Note on PDT:** Pattern Day Trader rules are about **day-trade count** under $25k, **not** “max 3 open holdings.” Open-position count here is capital-sized.

---

## Dashboard (Farzad-style monitor)

Public page sections:

- Portfolio value / total return / vs S&P 500 / P/L
- Portfolio equity chart (local paper curve; 1D–ALL ranges, scrub tooltip, buy/sell markers)
- Realized P/L, open P/L, capital deployed, closed trades, SPY
- How Hermes is trying to win (edge + hard limits + learning sample)
- Current holdings with stop/target
- What Hermes is thinking (plain English stance + triggers + owned cards)
- Decisions / stance
- Recent fills
- Top 5 Stop Loss (worst losers: entry/exit/P/L)
- Top 5 Take Profit (best winners: entry/exit/P/L)
- Trade reasoning cards (narrative + bullets + risk map)
- Improvements changelog
- Lists use show-more (3 at a time): fills, closed, activity, reasoning
- Hard refresh every 5 minutes
- **5m mark-and-publish** during NYSE hours: refresh holdings/SPY marks + push `dashboard-data.json` (no dual-LLM)

Data contract: `dashboard-data.json` (generated, committed for static hosting).

---

## Repo layout

```text
trading-bot/
├── config/
│   └── autonomy_config.json      # rules & limits
├── scripts/
│   ├── alpha_radar.py            # candidate scanner
│   ├── autotrader.py             # decisions + paper execution
│   ├── dual_llm.py               # live Grok/Claude desks + fallback
│   ├── paper_broker.py           # local account truth
│   ├── generate_dashboard_data.py
│   └── run_paper_session.py      # NYSE session runner (scan→trade→publish)
├── data/                         # local runtime (mostly gitignored)
│   ├── portfolio.json
│   ├── fills.jsonl
│   ├── closed_trades.jsonl
│   ├── order_ledger.jsonl
│   ├── consensus_log.jsonl
│   ├── equity_curve.jsonl
│   └── candidates.json
├── tests/
├── index.html                    # public dashboard UI
├── dashboard-data.json           # published snapshot
└── README.md
```

Secrets (`.env`, tokens, portfolio private state) stay gitignored. Do not commit account IDs, chat IDs, or credentials.

## Privacy / secrets checklist
- `.env` / `.env*` are gitignored
- API keys (`FMP_API_KEY`, `XAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) must never be committed or published
- Market data: FMP single-quote capable; **bulk multi-symbol uses yfinance** on free FMP (comma bulk = 402)
- 5m dashboard marks: holdings+SPY bulk via yfinance; browser never holds API keys
- FMP daily budget file local: `data/fmp_call_budget.json` (gitignored)
- Runtime ledgers under `data/` are local; public site gets sanitized `dashboard-data.json` only
- Before public push: scan for `xai-`, `sk-ant-`, bearer tokens, private keys, passwords

---

## Quick start (local)

```bash
cd ~/trading-bot

# 1) Scan candidates
python scripts/alpha_radar.py

# 2) Run one paper decision/execution cycle
python scripts/autotrader.py

# 3) Rebuild dashboard JSON
python scripts/generate_dashboard_data.py

# 4) Full session (scan + trade + dashboard [+ git push unless disabled])
python scripts/run_paper_session.py --force
# off-hours safe no-push:
PAPER_NO_PUSH=1 python scripts/run_paper_session.py --force
```

### Tests

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

---

## Automation (Hermes cron)

Active pattern:

- **Name:** `nyse-paper-session-15m` (scan + dual desk + trade)
- **Name:** `nyse-mark-publish-5m` (marks only + dashboard push)
  - Schedule: `*/5 9-15 * * 1-5` America/New_York window via Hermes cron
  - Quotes: **yfinance bulk** for holdings+SPY (0 FMP on free tier multi)
  - FMP: reserved/budgeted singles (`FMP_DAILY_CALL_LIMIT=200`), not full-universe spam
- **Schedule:** `*/15 9-15 * * 1-5` (cron window)
- **Runner:** Hermes script wrapper → `scripts/run_paper_session.py`
- **Session gate:** code enforces true NYSE regular hours `09:30–16:00 ET`
- **Delivery:** Telegram summary

Off-hours ticks should print `SKIP ...` and not open new risk.

Useful commands:

```bash
hermes cronjob list
# trigger one tick manually if your Hermes build supports it
```

---

## Telegram report contents

Each session summary is meant to be readable on mobile and includes:

- top scanner candidate
- decision clarity: **HOLD (no new fill)** vs **BUY/SELL fill**
- last real fill
- equity / cash / open P/L
- **holdings bullets** like the dashboard chips:
  - symbol, shares, last mark
  - open P/L `$` and `%`
  - stop / target
- publish status + dashboard URL

---

## Logs for analysis

| File | Purpose |
|------|---------|
| `data/fills.jsonl` | Actual paper BUY/SELL fills |
| `data/closed_trades.jsonl` | Closed trades + realized P/L + reason |
| `data/order_ledger.jsonl` | Decision → status trail (incl. HOLD) |
| `data/consensus_log.jsonl` | Agreement / validation outcomes |
| `data/portfolio.json` | Cash + open positions state |
| `data/candidates.json` | Latest Alpha Radar ranking |

Exit reasons you may see: `new_entry`, `stop_loss`, `take_profit`, `rotation`, `hold`.

---

## Safety / kill switch

Create this file to halt trading logic that honors it:

```bash
touch KILL_SWITCH.txt
```

Also keep:

- position size caps
- stop/target requirements
- session-hours gate
- IBKR MCP disabled until personal paper is ready

---

## Telegram policy

- Notify on **BUY/SELL paper fills only**
- Also notify on **API credit/quota exhaustion** (FMP / xAI / Anthropic), cooldown 6h per provider
- **No HOLD** / no routine disagreement spam
- Every trade alert includes the public dashboard URL
- Hermes 15m cron delivers stdout only when a fill happens (HOLD sessions stay silent)

## IBKR status (important)

- **Official IBKR remote MCP** and **local Client Portal bridge** are **not** the active account path right now.
- Hermes `ibkr` / `ibkr_remote` MCP entries should remain **enabled: false**.
- When a personal IBKR paper account is approved/active, broker truth can replace the local paper broker without throwing away Alpha Radar / dashboard / cron.

Do not leave stale `hermes mcp login ibkr_*` processes or `localhost:5000` Client Portal Java running in the background while parked.

---

## Deploy

Static hosting:

1. `index.html` + `dashboard-data.json` on `main`
2. GitHub repo → Vercel production
3. Session runner commits/pushes dashboard snapshot after market cycles (when publish is enabled)

Privacy checklist before any public change:

- no `.env` / tokens / account numbers
- no personal emails/usernames/chat IDs in tracked files
- prefer generic bot author metadata on public history

---

## Maintainer note

**Keep this README current whenever behavior, limits, dashboard sections, cron, or broker mode change.**  
README updates are part of the change, not a later cleanup task.

---

## Disclaimer

Experimental paper-trading research tooling. Not investment advice. You are solely responsible for any use of this software, including any future live trading.
