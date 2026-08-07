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


def build_trade_reasoning(fills, orders, closed):
    """Build Farzad-style trade reasoning cards from fills + order ledger."""
    order_by_key = {}
    for o in orders:
        if o.get("action") not in ("BUY", "SELL"):
            continue
        key = (o.get("symbol"), o.get("action"), o.get("timestamp"))
        order_by_key[key] = o
        # also index loosely by symbol+action latest
        order_by_key[(o.get("symbol"), o.get("action"), "latest")] = o

    closed_by_ts = {c.get("timestamp"): c for c in closed}
    cards = []
    for f in reversed(fills):
        if f.get("action") not in ("BUY", "SELL"):
            continue
        if f.get("symbol") in ("AAA",):  # skip test junk
            continue
        sym = f.get("symbol")
        action = f.get("action")
        px = float(f.get("price") or 0)
        qty = float(f.get("qty") or 0)
        notional = float(f.get("notional") or (px * qty) or 0)
        stop = f.get("stop_loss")
        target = f.get("take_profit")
        conf = f.get("confidence")
        thesis = f.get("thesis") or f.get("reason") or ""
        reason_code = f.get("reason") or ""

        # prefer reasoning from matching order ledger entry
        reasoning = None
        for o in reversed(orders):
            if o.get("symbol") == sym and o.get("action") == action:
                # close timestamps
                ot = o.get("timestamp") or ""
                ft = f.get("timestamp") or ""
                if ot[:16] == ft[:16] or abs(
                    (parse_ts(ot).timestamp() if parse_ts(ot) else 0)
                    - (parse_ts(ft).timestamp() if parse_ts(ft) else 0)
                ) < 5:
                    reasoning = o.get("reasoning")
                    if stop is None:
                        stop = o.get("stop_loss")
                    if target is None:
                        target = o.get("take_profit")
                    if conf is None:
                        conf = o.get("confidence")
                    if not thesis:
                        thesis = o.get("thesis") or ""
                    if not reason_code:
                        reason_code = o.get("reason_code") or ""
                    break

        if stop is None or target is None:
            # try closed trade companion / position defaults
            if action == "SELL":
                for c in closed:
                    if c.get("symbol") == sym and c.get("timestamp") == f.get("timestamp"):
                        reason_code = c.get("reason") or reason_code
                        break

        entry_for_rr = px
        if action == "SELL":
            for c in closed:
                if c.get("symbol") == sym and str(c.get("timestamp", ""))[:19] == str(f.get("timestamp", ""))[:19]:
                    entry_for_rr = float(c.get("avg_cost") or px)
                    break

        stop_f = float(stop) if stop is not None else round(entry_for_rr * 0.975, 2)
        target_f = float(target) if target is not None else round(entry_for_rr * 1.05, 2)
        conf_i = int(conf or (90 if action == "BUY" else 80))
        risk = max(abs(entry_for_rr - stop_f), 1e-9)
        reward = max(abs(target_f - entry_for_rr), 0.0)
        rr = round(reward / risk, 2)

        if reasoning and isinstance(reasoning, dict):
            narrative = reasoning.get("narrative") or reasoning.get("thesis") or thesis
            bullets = reasoning.get("bullets") or []
            risk_map = reasoning.get("risk_map") or {}
            headline = reasoning.get("headline") or (f"Bought ${sym}" if action == "BUY" else f"Sold ${sym}")
        else:
            if action == "BUY":
                headline = f"Bought ${sym}"
                narrative = thesis or f"{sym} entered from Alpha Radar ranked catalyst list."
                bullets = [
                    f"Paper fill size ${notional:.2f} under the single-name cap.",
                    f"Invalidation mapped under entry; upside mapped to target before the next decision loops.",
                ]
                if "Headline-driven" in thesis or "catalyst" in thesis.lower():
                    bullets.append("Catalyst/news rank was the primary selection input.")
            else:
                headline = f"Sold ${sym}"
                if reason_code == "rotation" or "rotat" in thesis.lower():
                    narrative = (
                        f"HERMES AUTO-TRADE: Exiting full {sym} position via gated rotation. {thesis}"
                    )
                    bullets = [
                        "Rotation recycled risk budget toward a higher-ranked setup.",
                        "Original stop/target were not necessarily tagged on this exit.",
                    ]
                elif reason_code == "stop_loss" or "stop" in thesis.lower():
                    narrative = thesis or f"Exiting {sym} on stop-loss at ${px:.2f}."
                    bullets = ["Hard stop hit; capital preservation over thesis hope."]
                elif reason_code == "take_profit" or "take-profit" in thesis.lower() or "target" in thesis.lower():
                    narrative = thesis or f"Exiting {sym} on take-profit at ${px:.2f}."
                    bullets = ["Target zone filled; gains locked."]
                else:
                    narrative = thesis or f"Exiting {sym} at ${px:.2f}."
                    bullets = [thesis or "Paper exit recorded."]
                if f.get("realized_pnl") is not None:
                    bullets.append(f"Realized P/L on fill: ${float(f.get('realized_pnl')):+.2f}.")
            risk_map = {
                "stop": stop_f,
                "target": target_f,
                "horizon_days": 5,
                "rr": rr,
                "confidence_10": max(1, min(10, int(round(conf_i / 10)))),
                "confidence": conf_i,
            }

        cards.append(
            {
                "timestamp": f.get("timestamp"),
                "action": action,
                "symbol": sym,
                "qty": qty,
                "price": px,
                "notional": round(notional, 2),
                "headline": headline,
                "side_label": "buy" if action == "BUY" else "sell",
                "narrative": narrative,
                "bullets": bullets,
                "risk_map": {
                    "stop": float(risk_map.get("stop", stop_f)),
                    "target": float(risk_map.get("target", target_f)),
                    "horizon_days": int(risk_map.get("horizon_days", 5)),
                    "rr": float(risk_map.get("rr", rr)),
                    "confidence_10": int(risk_map.get("confidence_10", max(1, min(10, int(round(conf_i / 10)))))),
                    "confidence": int(risk_map.get("confidence", conf_i)),
                },
                "reason_code": reason_code or f.get("reason") or ("new_entry" if action == "BUY" else "exit"),
                "realized_pnl": f.get("realized_pnl"),
                "status": f.get("status"),
            }
        )
    return cards



def build_thinking(holdings, latest_decision, latest_candidate, snap, now, cfg):
    """Farzad-style plain-English stance block."""
    held = [h for h in holdings if h.get("symbol")]
    syms = [h.get("symbol") for h in held]
    act = (latest_decision or {}).get("action") or "HOLD"
    dec_sym = (latest_decision or {}).get("symbol")
    top = (latest_candidate or {}).get("symbol")
    cash = float((snap or {}).get("cash") or 0)
    max_pos = int(((cfg or {}).get("position_limits") or {}).get("max_positions") or 5)
    min_pos = float(((cfg or {}).get("position_limits") or {}).get("min_position_size_usd") or 50)

    if act == "BUY" and dec_sym:
        badge = f"Looking to buy {dec_sym}"
        headline = f"Buying pressure on {dec_sym}."
        plain = (
            f"Right now Hermes wants exposure to {dec_sym}. "
            f"The desk agreed on a buy only after ranked catalyst evidence and risk checks cleared."
        )
    elif act == "SELL" and dec_sym:
        badge = f"Exiting {dec_sym}"
        headline = f"Selling {dec_sym}."
        plain = (
            f"Right now Hermes is exiting {dec_sym}. "
            f"That can be stop, target, or gated rotation when a stronger setup needs risk budget."
        )
    else:
        if syms:
            joined = ", ".join(syms)
            badge = "Holding what it owns"
            headline = f"Holding {joined}. No new trade right now."
            plain = (
                f"Right now Hermes is holding {joined} and not forcing a new trade. "
                f"Nothing new looked clean enough, so the safe move is patience."
            )
        else:
            badge = "Waiting in cash"
            headline = "No open holdings. Waiting for a clean setup."
            plain = (
                "Right now Hermes is in cash and not forcing a trade. "
                "It will only act when a ranked setup clears dual agreement and risk checks."
            )

    triggers = []
    for h in held:
        sym = h.get("symbol")
        avg = h.get("avg_cost")
        stop = h.get("stop")
        target = h.get("target")
        try:
            avg_s = f"${float(avg):.2f}"
            stop_s = f"${float(stop):.2f}"
            tgt_s = f"${float(target):.2f}"
        except Exception:
            avg_s, stop_s, tgt_s = str(avg), str(stop), str(target)
        triggers.append(
            f"It owns {sym}, bought near {avg_s}. Simple plan: sell if it falls to about {stop_s}, or take profit near {tgt_s}."
        )

    if len(held) >= max_pos:
        triggers.append(
            f"Book is at max open names ({max_pos}). A new buy needs a free slot, enough cash, or a gated rotation."
        )
    elif cash < min_pos:
        triggers.append(
            f"Cash is only about ${cash:.2f}, below the ${min_pos:.0f} minimum new-buy size, so fresh entries wait."
        )
    else:
        triggers.append(
            "Only buy something new if the story is strong, the stock trades enough volume, and the upside is clearly bigger than the downside."
        )
    if top and top not in syms:
        triggers.append(
            f"Top scanner name right now is {top}, but it still has to clear dual agreement and risk code before any fill."
        )
    triggers.append("If nothing looks clean, do nothing. Sitting in cash or holdings is allowed.")

    owned_cards = []
    for h in held:
        owned_cards.append(
            {
                "symbol": h.get("symbol"),
                "qty": h.get("qty"),
                "avg_cost": h.get("avg_cost"),
                "stop": h.get("stop"),
                "target": h.get("target"),
                "last": h.get("last"),
                "open_pnl": h.get("open_pnl"),
                "open_pnl_pct": h.get("open_pnl_pct"),
                "line": (
                    f"Bought near ${float(h.get('avg_cost') or 0):.2f} · "
                    f"Sell safety line ${float(h.get('stop') or 0):.2f} · "
                    f"Profit target ${float(h.get('target') or 0):.2f}"
                ),
                "plan": (
                    f"stop {float(h.get('stop') or 0):.1f} or target {float(h.get('target') or 0):.1f}"
                ),
            }
        )

    return {
        "badge": badge,
        "headline": headline,
        "plain_english": plain,
        "triggers": triggers,
        "owned": owned_cards,
        "last_checked": now.isoformat(),
        "cadence_note": "Checked about every 15 minutes while the market is open",
        "footer": "Written for normal people. Updated on each live check. Private account details are removed.",
        "action": act,
        "symbols_held": syms,
    }



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

    # Closed results newest first (Farzad-style list)
    closed_sorted = sorted(
        closed,
        key=lambda r: r.get("timestamp") or "",
        reverse=True,
    )
    closed_results = []
    for row in closed_sorted:
        if row.get("symbol") in ("AAA",):
            continue
        avg = float(row.get("avg_cost") or 0)
        qty = float(row.get("qty") or 0)
        pnl = row.get("realized_pnl")
        try:
            pnl_f = float(pnl) if pnl is not None else None
        except Exception:
            pnl_f = None
        basis = avg * qty if avg and qty else None
        pct = None
        if pnl_f is not None and basis and abs(basis) > 1e-9:
            pct = round(pnl_f / abs(basis) * 100.0, 2)
        closed_results.append(
            {
                "timestamp": row.get("timestamp"),
                "symbol": row.get("symbol"),
                "qty": row.get("qty"),
                "avg_cost": row.get("avg_cost"),
                "exit_price": row.get("exit_price"),
                "proceeds": row.get("proceeds"),
                "realized_pnl": pnl_f,
                "realized_pnl_pct": pct,
                "reason": row.get("reason"),
                "thesis": row.get("thesis"),
                "opened_at": row.get("opened_at"),
                "hold_seconds": row.get("hold_seconds"),
            }
        )
    closed_recent = closed_results[:20]

    thinking = build_thinking(holdings, latest_decision, latest_candidate, snap, now, cfg)
    stance = thinking.get("headline") or "Patient — waiting for a clean ranked setup."
    if latest_decision and latest_decision.get("thesis") and latest_decision.get("action") in ("BUY", "SELL"):
        # keep short stance string for older consumers
        stance = f"{thinking.get('headline')} {(latest_decision.get('thesis') or '')}".strip()

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
        "thinking": thinking,
        "activity": activity,
        "recent_trades": recent_trades,
        "trade_reasoning": build_trade_reasoning(fills, orders, closed),
        "closed_trades": closed_recent,
        "closed_results": closed_results,
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
        "consensus": {
            "require_dual_model_agreement": bool(
                cfg.get("consensus_rules", {}).get("require_dual_model_agreement", True)
            ),
            "model_1": cfg.get("consensus_rules", {}).get("model_1", "grok-4.5"),
            "model_1_effort": cfg.get("consensus_rules", {}).get("model_1_effort"),
            "model_2": cfg.get("consensus_rules", {}).get("model_2", "claude-sonnet-5"),
            "min_confidence": cfg.get("consensus_rules", {}).get("min_confidence", 70),
            "max_candidates_to_llm": cfg.get("consensus_rules", {}).get("max_candidates_to_llm", 8),
            "log_all_disagreements": bool(
                cfg.get("consensus_rules", {}).get("log_all_disagreements", True)
            ),
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
