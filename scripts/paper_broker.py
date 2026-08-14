#!/usr/bin/env python3
"""
Local simulated paper broker.

This is the account source-of-truth until IBKR personal paper is active.
Persists cash, positions, fills, and closed trades under data/.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import market_data
except Exception:  # pragma: no cover
    market_data = None
try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PORTFOLIO_PATH = DATA / "portfolio.json"
FILLS_PATH = DATA / "fills.jsonl"
CLOSED_PATH = DATA / "closed_trades.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Max age (seconds) for a cached SPY mark before it's treated as stale and
# re-quoted. Mark cadence is 10m; 30m headroom avoids re-quoting every cycle
# while still discarding cross-session/Friday prints.
SPY_MARK_MAX_AGE = 1800


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def quote(symbol: str) -> float | None:
    """Best-effort last price (IBKR gateway primary, yfinance fallback)."""
    if market_data is not None:
        try:
            px = market_data.quote(symbol)
            if px is not None:
                return float(px)
        except Exception:
            pass
    # last-resort direct yfinance
    if yf is None:
        return None
    try:
        t = yf.Ticker(symbol)
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            px = fi.get("lastPrice") or fi.get("last_price") or fi.get("regularMarketPrice")
            if px:
                return float(px)
        hist = t.history(period="5d")
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        return None
    return None


class PaperBroker:
    def __init__(self, starting_cash: float = 1000.0, path: Path = PORTFOLIO_PATH):
        self.path = path
        self.starting_cash = float(starting_cash)
        self.state = self._load_or_init()

    def _default_state(self) -> dict[str, Any]:
        now = utc_now()
        return {
            "version": 1,
            "mode": "SIMULATED_PAPER",
            "currency": "USD",
            "started_at": now,
            "updated_at": now,
            "starting_cash": self.starting_cash,
            "cash": self.starting_cash,
            "realized_pnl": 0.0,
            "benchmark": {
                "symbol": "SPY",
                "start_price": None,
                "start_at": now,
            },
            "positions": {},  # symbol -> position dict
            "stats": {
                "closed_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "buys": 0,
                "sells": 0,
            },
        }

    def _load_or_init(self) -> dict[str, Any]:
        DATA.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            state = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            state = self._default_state()
            # Capture SPY baseline once
            spy = quote("SPY")
            if spy:
                state["benchmark"]["start_price"] = spy
            self._save(state)
            return state

        # Ensure required keys exist for older files
        base = self._default_state()
        for k, v in base.items():
            if k not in state:
                state[k] = v
        if not state.get("benchmark", {}).get("start_price"):
            spy = quote("SPY")
            if spy:
                state.setdefault("benchmark", {})["start_price"] = spy
        return state

    def _save(self, state: dict | None = None) -> None:
        if state is None:
            state = self.state
        state["updated_at"] = utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        self.state = state

    def mark_to_market(
        self,
        symbols: list[str] | None = None,
        prefetched: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Mark positions. Prefer one bulk prefetch (IBKR/yfinance layer) over N singles."""
        marks: dict[str, float] = {}
        pos = self.state.get("positions", {})
        targets = list(symbols) if symbols is not None else list(pos.keys())
        # always include open positions if symbols partially provided
        for sym in list(pos.keys()):
            if sym not in targets:
                targets.append(sym)

        price_map: dict[str, float] = {}
        if prefetched:
            for k, v in prefetched.items():
                try:
                    if v is not None:
                        price_map[str(k).upper()] = float(v)
                except Exception:
                    pass
        missing = [s for s in targets if s.upper() not in price_map]
        if missing and market_data is not None:
            try:
                bulk = market_data.quotes_bulk(missing, prefer="ibkr")
                for s, row in (bulk or {}).items():
                    if row and row.get("price") is not None:
                        price_map[str(s).upper()] = float(row["price"])
            except Exception:
                pass
        for sym in targets:
            su = sym.upper()
            px = price_map.get(su)
            if px is None:
                px = quote(sym)
            if px is None:
                if su in pos and pos[su].get("last_price"):
                    marks[su] = float(pos[su]["last_price"])
                elif sym in pos and pos[sym].get("last_price"):
                    marks[sym] = float(pos[sym]["last_price"])
                continue
            marks[su] = float(px)
            p = pos.get(su) or pos.get(sym)
            if p:
                qty = float(p["qty"])
                avg = float(p["avg_cost"])
                p["last_price"] = float(px)
                p["market_value"] = round(qty * float(px), 2)
                p["open_pnl"] = round((float(px) - avg) * qty, 2)
                p["open_pnl_pct"] = round(((float(px) - avg) / avg) * 100, 2) if avg else 0.0
                p["marked_at"] = utc_now()
        self._save()
        return marks

    def snapshot(self, skip_mark: bool = False) -> dict[str, Any]:
        if not skip_mark:
            self.mark_to_market()
        positions = []
        open_pnl = 0.0
        market_value = 0.0
        for sym, p in self.state.get("positions", {}).items():
            qty = float(p["qty"])
            if qty <= 0:
                continue
            mv = float(p.get("market_value") or qty * float(p.get("last_price") or p["avg_cost"]))
            op = float(p.get("open_pnl") or 0.0)
            open_pnl += op
            market_value += mv
            positions.append(
                {
                    "symbol": sym,
                    "qty": qty,
                    "avg_cost": float(p["avg_cost"]),
                    "current_price": float(p.get("last_price") or p["avg_cost"]),
                    "market_value": mv,
                    "unrealized_pnl": op,
                    "open_pnl_pct": float(p.get("open_pnl_pct") or 0.0),
                    "stop_loss": p.get("stop_loss"),
                    "take_profit": p.get("take_profit"),
                    "thesis": p.get("thesis"),
                    "opened_at": p.get("opened_at"),
                }
            )

        cash = float(self.state["cash"])
        equity = round(cash + market_value, 2)
        starting = float(self.state.get("starting_cash") or self.starting_cash)
        realized = float(self.state.get("realized_pnl") or 0.0)
        total_pnl = round(equity - starting, 2)
        total_return_pct = round((equity / starting - 1.0) * 100, 2) if starting else 0.0

        # SPY benchmark. Prefer a cached mark (mark_and_publish sets spy_last +
        # spy_marked_at via prefetch); only hit the quote source when skip_mark
        # (already-marked) and no fresh cached value exists, or when re-marking.
        spy_now = self.state.get("spy_last")
        marked_at = self.state.get("spy_marked_at")
        spy_fresh = False
        if marked_at:
            try:
                mt = datetime.fromisoformat(marked_at.replace("Z", "+00:00"))
                spy_fresh = (datetime.now(timezone.utc) - mt).total_seconds() <= SPY_MARK_MAX_AGE
            except Exception:
                spy_fresh = False
        # Ignore a stale spy_last (missing timestamp or older than the mark cadence).
        if spy_now is None or not skip_mark or not spy_fresh:
            spy_now = quote("SPY")
            if spy_now is not None:
                self.state["spy_last"] = float(spy_now)
                self.state["spy_marked_at"] = utc_now()
        spy_start = self.state.get("benchmark", {}).get("start_price")
        spy_return_pct = None
        vs_spy_pct = None
        if spy_now and spy_start:
            spy_return_pct = round((float(spy_now) / float(spy_start) - 1.0) * 100, 2)
            vs_spy_pct = round(total_return_pct - spy_return_pct, 2)

        closed = int(self.state.get("stats", {}).get("closed_trades") or 0)
        wins = int(self.state.get("stats", {}).get("winning_trades") or 0)
        win_rate = round((wins / closed) * 100, 2) if closed else 0.0

        return {
            "account_id": "SIM_PAPER_LOCAL",
            "mode": "SIMULATED_PAPER",
            "buying_power": round(cash, 2),
            "cash": round(cash, 2),
            "equity": equity,
            "starting_cash": starting,
            "market_value": round(market_value, 2),
            "capital_deployed": round(market_value, 2),
            "realized_pnl": round(realized, 2),
            "open_pnl": round(open_pnl, 2),
            "total_pnl": total_pnl,
            "total_return_pct": total_return_pct,
            "spy_price": spy_now,
            "spy_start_price": spy_start,
            "spy_return_pct": spy_return_pct,
            "vs_spy_pct": vs_spy_pct,
            "closed_trades": closed,
            "winning_trades": wins,
            "win_rate_pct": win_rate,
            "positions": positions,
            "pending_orders": [],
            "updated_at": self.state.get("updated_at"),
            "started_at": self.state.get("started_at"),
        }

    def buy(
        self,
        symbol: str,
        qty: float,
        price: float,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        thesis: str = "",
        confidence: int | None = None,
    ) -> dict[str, Any]:
        qty = float(qty)
        price = float(price)
        if qty <= 0 or price <= 0:
            raise ValueError("qty and price must be positive")

        cost = round(qty * price, 2)
        cash = float(self.state["cash"])
        if cost > cash + 0.01:
            raise ValueError(f"insufficient cash: need {cost:.2f}, have {cash:.2f}")

        positions = self.state.setdefault("positions", {})
        existing = positions.get(symbol)
        if existing:
            old_qty = float(existing["qty"])
            old_cost = old_qty * float(existing["avg_cost"])
            new_qty = old_qty + qty
            avg = (old_cost + cost) / new_qty
            existing.update(
                {
                    "qty": round(new_qty, 6),
                    "avg_cost": round(avg, 4),
                    "last_price": price,
                    "market_value": round(new_qty * price, 2),
                    "open_pnl": round((price - avg) * new_qty, 2),
                    "stop_loss": stop_loss if stop_loss is not None else existing.get("stop_loss"),
                    "take_profit": take_profit if take_profit is not None else existing.get("take_profit"),
                    "thesis": thesis or existing.get("thesis"),
                    "confidence": confidence if confidence is not None else existing.get("confidence"),
                    "updated_at": utc_now(),
                }
            )
        else:
            positions[symbol] = {
                "symbol": symbol,
                "qty": round(qty, 6),
                "avg_cost": round(price, 4),
                "last_price": price,
                "market_value": cost,
                "open_pnl": 0.0,
                "open_pnl_pct": 0.0,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "thesis": thesis,
                "confidence": confidence,
                "opened_at": utc_now(),
                "updated_at": utc_now(),
            }

        self.state["cash"] = round(cash - cost, 2)
        self.state.setdefault("stats", {})
        self.state["stats"]["buys"] = int(self.state["stats"].get("buys") or 0) + 1

        fill = {
            "timestamp": utc_now(),
            "action": "BUY",
            "symbol": symbol,
            "qty": qty,
            "price": price,
            "notional": cost,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "thesis": thesis,
            "confidence": confidence,
            "status": "FILLED_PAPER",
            "cash_after": self.state["cash"],
        }
        _append_jsonl(FILLS_PATH, fill)
        self._save()
        return fill

    def sell(
        self,
        symbol: str,
        qty: float | None = None,
        price: float | None = None,
        *,
        reason: str = "manual",
        thesis: str = "",
    ) -> dict[str, Any]:
        positions = self.state.setdefault("positions", {})
        pos = positions.get(symbol)
        if not pos:
            raise ValueError(f"no position in {symbol}")

        hold_qty = float(pos["qty"])
        sell_qty = hold_qty if qty is None else min(hold_qty, float(qty))
        if sell_qty <= 0:
            raise ValueError("sell qty must be positive")

        px = float(price if price is not None else (pos.get("last_price") or quote(symbol) or pos["avg_cost"]))
        proceeds = round(sell_qty * px, 2)
        avg = float(pos["avg_cost"])
        realized = round((px - avg) * sell_qty, 2)

        self.state["cash"] = round(float(self.state["cash"]) + proceeds, 2)
        self.state["realized_pnl"] = round(float(self.state.get("realized_pnl") or 0.0) + realized, 2)

        remaining = round(hold_qty - sell_qty, 6)
        closed_trade = {
            "timestamp": utc_now(),
            "symbol": symbol,
            "qty": sell_qty,
            "avg_cost": avg,
            "exit_price": px,
            "proceeds": proceeds,
            "realized_pnl": realized,
            "reason": reason,
            "thesis": thesis or pos.get("thesis"),
            "opened_at": pos.get("opened_at"),
            "hold_seconds": None,
        }
        try:
            if pos.get("opened_at"):
                opened = datetime.fromisoformat(pos["opened_at"].replace("Z", "+00:00"))
                closed_trade["hold_seconds"] = int((datetime.now(timezone.utc) - opened).total_seconds())
        except Exception:
            pass

        _append_jsonl(CLOSED_PATH, closed_trade)
        stats = self.state.setdefault("stats", {})
        stats["sells"] = int(stats.get("sells") or 0) + 1
        stats["closed_trades"] = int(stats.get("closed_trades") or 0) + 1
        if realized >= 0:
            stats["winning_trades"] = int(stats.get("winning_trades") or 0) + 1
        else:
            stats["losing_trades"] = int(stats.get("losing_trades") or 0) + 1

        if remaining <= 1e-8:
            del positions[symbol]
        else:
            pos["qty"] = remaining
            pos["last_price"] = px
            pos["market_value"] = round(remaining * px, 2)
            pos["open_pnl"] = round((px - avg) * remaining, 2)
            pos["updated_at"] = utc_now()

        fill = {
            "timestamp": utc_now(),
            "action": "SELL",
            "symbol": symbol,
            "qty": sell_qty,
            "price": px,
            "notional": proceeds,
            "realized_pnl": realized,
            "reason": reason,
            "thesis": thesis or pos.get("thesis") if remaining > 0 else closed_trade.get("thesis"),
            "status": "FILLED_PAPER",
            "cash_after": self.state["cash"],
        }
        _append_jsonl(FILLS_PATH, fill)
        self._save()
        return fill

def load_broker(starting_cash: float = 1000.0) -> PaperBroker:
    return PaperBroker(starting_cash=starting_cash)


if __name__ == "__main__":
    b = load_broker()
    print(json.dumps(b.snapshot(), indent=2))
