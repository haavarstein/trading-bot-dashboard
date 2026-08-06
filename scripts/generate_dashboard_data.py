#!/usr/bin/env python3
"""Generate Farzad-style public dashboard data from local paper portfolio + ledgers."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "dashboard-data.json"

sys_path_note = str(ROOT / "scripts")
import sys

sys.path.insert(0, sys_path_note)
from paper_broker import load_broker  # noqa: E402


def read_jsonl(path: Path):
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


def parse_ts(s: str):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def main():
    now = datetime.now(timezone.utc)
    today = now.date()

    cfg = json.loads((ROOT / "config" / "autonomy_config.json").read_text(encoding="utf-8"))
    starting = float(cfg.get("account", {}).get("starting_capital", 1000))
    broker = load_broker(starting_cash=starting)
    snap = broker.snapshot()

    candidates_blob = {}
    if (DATA / "candidates.json").exists():
        candidates_blob = json.loads((DATA / "candidates.json").read_text(encoding="utf-8"))
    candidates = candidates_blob.get("candidates", [])

    consensus = read_jsonl(DATA / "consensus_log.jsonl")
    orders = read_jsonl(DATA / "order_ledger.jsonl")
    fills = read_jsonl(DATA / "fills.jsonl")
    closed = read_jsonl(DATA / "closed_trades.jsonl")

    today_consensus = [r for r in consensus if (parse_ts(r.get("timestamp", "")) and parse_ts(r.get("timestamp", "")).date() == today)]
    today_orders = [r for r in orders if (parse_ts(r.get("timestamp", "")) and parse_ts(r.get("timestamp", "")).date() == today)]
    today_fills = [r for r in fills if (parse_ts(r.get("timestamp", "")) and parse_ts(r.get("timestamp", "")).date() == today)]

    valid_count = sum(1 for r in today_consensus if r.get("validation", {}).get("valid") is True)
    blocked_count = sum(1 for r in today_consensus if r.get("validation", {}).get("valid") is False)
    consensus_count = sum(1 for r in today_consensus if r.get("consensus") is True)

    latest_candidate = candidates[0] if candidates else {}
    latest_decision = None
    for row in reversed(today_consensus or consensus):
        if row.get("model1"):
            latest_decision = row.get("model1")
            break

    # Activity feed
    activity = []
    for row in today_fills[-12:]:
        activity.append(
            {
                "type": "fill",
                "symbol": row.get("symbol"),
                "status": str(row.get("status", "filled")).lower(),
                "headline": f"{row.get('action')} {row.get('symbol')} @ ${float(row.get('price') or 0):.2f}",
                "detail": row.get("thesis") or row.get("reason") or "",
                "timestamp": row.get("timestamp"),
            }
        )
    for row in today_consensus[-8:]:
        sym = row.get("model1", {}).get("symbol", "N/A")
        action = row.get("model1", {}).get("action", "")
        validation = row.get("validation", {})
        valid = validation.get("valid")
        status = "validated" if valid else "blocked"
        if row.get("model1", {}).get("action") == "HOLD" and valid:
            status = "hold"
        activity.append(
            {
                "type": "consensus",
                "symbol": sym,
                "status": status,
                "headline": f"{action} {sym} — {status}",
                "detail": validation.get("reason") or row.get("model1", {}).get("thesis", ""),
                "timestamp": row.get("timestamp"),
            }
        )
    activity.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    activity = activity[:12]

    holdings = []
    for p in snap.get("positions") or []:
        holdings.append(
            {
                "symbol": p["symbol"],
                "qty": p["qty"],
                "avg_cost": p["avg_cost"],
                "last": p["current_price"],
                "market_value": p["market_value"],
                "open_pnl": p["unrealized_pnl"],
                "open_pnl_pct": p.get("open_pnl_pct"),
                "stop": p.get("stop_loss"),
                "target": p.get("take_profit"),
                "thesis": p.get("thesis"),
            }
        )

    recent_trades = []
    for row in (fills or [])[-10:][::-1]:
        recent_trades.append(
            {
                "timestamp": row.get("timestamp"),
                "action": row.get("action"),
                "symbol": row.get("symbol"),
                "qty": row.get("qty"),
                "price": row.get("price"),
                "notional": row.get("notional"),
                "realized_pnl": row.get("realized_pnl"),
                "reason": row.get("reason"),
                "thesis": row.get("thesis"),
                "status": row.get("status"),
            }
        )

    closed_recent = closed[-8:][::-1]

    stance = "Patient — waiting for a clean ranked setup."
    if latest_decision:
        act = latest_decision.get("action")
        sym = latest_decision.get("symbol")
        thesis = latest_decision.get("thesis") or ""
        if act == "BUY":
            stance = f"Buying pressure on {sym}. {thesis}"
        elif act == "SELL":
            stance = f"Exiting {sym}. {thesis}"
        elif act == "HOLD":
            stance = f"Holding / no new risk. {thesis}"

    payload = {
        "generated_at": now.isoformat(),
        "mode": "SIMULATED_PAPER",
        "scanner": candidates_blob.get("scanner", "unknown"),
        "summary": {
            "portfolio_value": snap.get("equity"),
            "starting_cash": snap.get("starting_cash"),
            "total_return_pct": snap.get("total_return_pct"),
            "total_pnl": snap.get("total_pnl"),
            "realized_pnl": snap.get("realized_pnl"),
            "open_pnl": snap.get("open_pnl"),
            "capital_deployed": snap.get("capital_deployed"),
            "cash": snap.get("cash"),
            "closed_trades": snap.get("closed_trades"),
            "win_rate_pct": snap.get("win_rate_pct"),
            "vs_spy_pct": snap.get("vs_spy_pct"),
            "spy_return_pct": snap.get("spy_return_pct"),
            "started_at": snap.get("started_at"),
            "updated_at": snap.get("updated_at"),
        },
        "benchmark": {
            "symbol": "SPY",
            "return_pct": snap.get("spy_return_pct"),
            "price": snap.get("spy_price"),
            "start_price": snap.get("spy_start_price"),
        },
        "stats": {
            "candidate_count": len(candidates),
            "consensus_count_today": consensus_count,
            "validated_count_today": valid_count,
            "blocked_count_today": blocked_count,
            "fills_today": len(today_fills),
            "simulated_orders_today": len(today_orders),
            "top_symbol_today": Counter(
                [r.get("symbol") for r in today_fills if r.get("symbol")]
            ).most_common(1)[0][0]
            if today_fills
            else ((latest_candidate or {}).get("symbol")),
        },
        "holdings": holdings,
        "latest_candidate": latest_candidate,
        "latest_decision": latest_decision,
        "stance": stance,
        "activity": activity,
        "recent_trades": recent_trades,
        "closed_trades": closed_recent,
        "top_candidates": candidates[:5],
        "limits": {
            "max_position_usd": cfg.get("position_limits", {}).get("max_position_size_usd"),
            "max_positions": cfg.get("position_limits", {}).get("max_positions"),
            "min_rr": cfg.get("order_limits", {}).get("min_risk_reward_ratio"),
            "max_daily_loss_pct": cfg.get("risk_rules", {}).get("max_loss_per_day_pct"),
            "max_daily_loss_usd": round(
                float(cfg.get("account", {}).get("starting_capital", 1000))
                * float(cfg.get("risk_rules", {}).get("max_loss_per_day_pct", 5))
                / 100.0,
                2,
            ),
            "max_drawdown_pct": cfg.get("risk_rules", {}).get("max_drawdown_pct", 8),
            "allow_rotation": bool(cfg.get("execution_rules", {}).get("allow_rotation", False)),
            "rotation_min_hold_minutes": cfg.get("execution_rules", {}).get("rotation_min_hold_minutes"),
            "rotation_min_score": cfg.get("execution_rules", {}).get("rotation_min_score"),
        },
        "learning": {
            "candidate_signals_logged": len(candidates),
            "fills_logged": len(fills),
            "closed_outcomes": len(closed),
            "consensus_events": len(consensus),
            "note": "Paper sample is provisional until large enough for durable edge review.",
        },
        "system": {
            "broker": "local_paper_broker",
            "ibkr_mcp": "paused",
            "cadence": "every 15m during NYSE regular hours",
            "note": "Simulated paper account with real market marks. Not IBKR-confirmed.",
        },
    }

    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(str(OUT))


if __name__ == "__main__":
    main()
