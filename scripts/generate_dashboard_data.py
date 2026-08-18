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

# Earliest fill/close date included on the dashboard (equity curve AND the
# closed-trade boards: recent fills, Top 5 Take Profit, Top 5 Stop Loss).
# Trades closed before this are early test/validation days and are excluded.
MIN_FILL_DATE = "2026-08-14"

sys_path_note = str(ROOT / "scripts")
import sys

sys.path.insert(0, sys_path_note)
from paper_broker import load_broker  # noqa: E402
try:
    import market_data  # noqa: E402
except Exception:
    market_data = None


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



def _build_model_chips(row):
    """Model tags for the thinking card (e.g. 'J Grok 4.3' 'HOLD CASH 60%').
    Juniors prefixed J, seniors S, from the latest consensus row."""
    if not row:
        return []
    chips = []
    # order: juniors then seniors
    order = [
        ("junior1", "J"), ("junior2", "J"), ("junior3", "J"), ("junior4", "J"),
        ("senior1", "S"), ("senior2", "S"), ("senior3", "S"),
    ]
    for key, prefix in order:
        v = row.get(key) or {}
        if not isinstance(v, dict):
            continue
        model = str(v.get("model") or "").strip()
        if not model:
            continue
        action = str(v.get("action") or "?").upper()
        symbol = str(v.get("symbol") or "").upper()
        conf = v.get("confidence")
        chips.append({
            "prefix": prefix,
            "model": model,
            "action": action,
            "symbol": symbol,
            "confidence": int(conf) if isinstance(conf, (int, float)) else None,
            "tone": "buy" if action == "BUY" else ("sell" if action == "SELL" else "hold"),
        })
    return chips


def build_thinking(holdings, latest_decision, latest_candidate, snap, now, cfg, latest_consensus_row=None):
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
    elif act == "SELL" and dec_sym and dec_sym in syms:
        # Only show an active "Selling X" while X is still held (decision pending
        # or in flight). If the SELL already executed and X is gone, fall through
        # to the current-state branch below instead of describing a stale exit.
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
                "It will only act when a ranked setup clears the senior 2-of-3 live-majority gate and risk checks."
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
            f"Top scanner name right now is {top}, but it still has to clear the senior 2-of-3 live-majority gate and risk code before any fill."
        )
    sol_model = ((cfg or {}).get("consensus_rules") or {}).get("sol_model", "gpt-5.6-sol")
    sol_on = bool(((cfg or {}).get("consensus_rules") or {}).get("sol_chart_validator_enabled", False))
    if sol_on:
        triggers.append(
            f"On any trade that escalates to seniors, the {sol_model} chart validator reads a real candlestick chart to sanity-check support/resistance, entry, and stop before a fill."
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
        "model_chips": _build_model_chips(latest_consensus_row),
    }



def _fill_day(fill) -> str:
    return str(fill.get("timestamp") or "")[:10]


def _is_zero_hold_roundtrip(buy: dict, sell: dict) -> bool:
    """True when a BUY/SELL pair never actually held (broken-session artifact)."""
    try:
        if int(sell.get("hold_seconds") or -1) == 0:
            return True
    except (TypeError, ValueError):
        pass
    opened = sell.get("opened_at") or (buy or {}).get("timestamp")
    closed = sell.get("timestamp")
    if opened and closed and str(opened)[:19] == str(closed)[:19]:
        return True
    bt = parse_ts((buy or {}).get("timestamp") or "")
    st = parse_ts(closed or "")
    if bt is None or st is None:
        return False
    hold = (st - bt).total_seconds()
    return 0 <= hold <= 1.0


def _burst_symbol_days(fills) -> set:
    """(date, symbol) pairs that are a same-day high-frequency / zero-hold burst."""
    by_key: dict[tuple[str, str], list] = {}
    for f in fills:
        day = _fill_day(f)
        sym = f.get("symbol")
        if day and sym:
            by_key.setdefault((day, sym), []).append(f)

    bursts = set()
    for key, rows in by_key.items():
        rows = sorted(rows, key=lambda r: r.get("timestamp") or "")
        last_buy = None
        zero_holds = 0
        for f in rows:
            if f.get("action") == "BUY":
                last_buy = f
            elif f.get("action") == "SELL" and last_buy is not None:
                if _is_zero_hold_roundtrip(last_buy, f):
                    zero_holds += 1
                last_buy = None
        # Generic: many zero-hold round-trips, or a same-symbol same-day spray.
        if zero_holds >= 3 or len(rows) >= 8:
            bursts.add(key)
    return bursts


def _dedupe_same_day_fills(fills) -> list:
    """Keep the first fill of an identical same-day (symbol, action, qty, price)."""
    seen = set()
    out = []
    for f in sorted(fills, key=lambda r: r.get("timestamp") or ""):
        try:
            qty = round(float(f.get("qty") or 0), 6)
            px = round(float(f.get("price") or 0), 4)
        except (TypeError, ValueError):
            qty, px = f.get("qty"), f.get("price")
        key = (_fill_day(f), f.get("symbol"), f.get("action"), qty, px)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def filter_fills_for_equity(fills) -> list:
    """Fills eligible to appear on the equity curve (real-run, non-burst)."""
    eligible = []
    for f in fills or []:
        if f.get("action") not in ("BUY", "SELL"):
            continue
        if f.get("symbol") in ("AAA",):
            continue
        if _fill_day(f) < MIN_FILL_DATE:
            continue
        eligible.append(f)
    bursts = _burst_symbol_days(eligible)
    kept = [f for f in eligible if (_fill_day(f), f.get("symbol")) not in bursts]
    return _dedupe_same_day_fills(kept)


def _stored_mark_point(row: dict) -> dict | None:
    ts = row.get("t") or row.get("timestamp")
    eq = row.get("equity") if row.get("equity") is not None else row.get("value")
    if not ts or eq is None:
        return None
    try:
        equity = round(float(eq), 2)
    except (TypeError, ValueError):
        return None
    return {
        "t": ts,
        "equity": equity,
        "cash": row.get("cash"),
        "event": row.get("event") or "mark",
    }


def _equity_at_or_before(marks: list, ts: str, default: float) -> float:
    eq = default
    for p in marks:
        if (p.get("t") or "") <= ts:
            eq = p["equity"]
        else:
            break
    return eq


def build_equity_curve(fills, snap, starting_cash: float) -> dict:
    """
    Build the paper equity curve.

    data/equity_curve.jsonl broker M2M marks are the authoritative series.
    Filtered fills are event annotations only — they do not reconstruct
    equity (a broken session's fill replay double-counts position value).
    Also appends a durable point to data/equity_curve.jsonl each generate.
    """
    snap = snap or {}
    started = snap.get("started_at")
    start_eq = float(starting_cash)
    by_t: dict[str, dict] = {}

    if started:
        by_t[started] = {
            "t": started,
            "equity": round(start_eq, 2),
            "cash": round(start_eq, 2),
            "event": "start",
        }

    # PRIMARY: durable clean marks (same source weekly_readiness uses).
    curve_path = DATA / "equity_curve.jsonl"
    stored_rows = read_jsonl(curve_path) if curve_path.exists() else []
    stored_marks = []
    for row in stored_rows:
        pt = _stored_mark_point(row)
        if pt is None:
            continue
        by_t[pt["t"]] = pt
        stored_marks.append(pt)
    stored_marks.sort(key=lambda p: p.get("t") or "")

    # SECONDARY: filtered fill events. Equity comes from the last stored mark
    # (or starting cash), never from a cash/position replay of the fill tape.
    last_cash = start_eq
    for f in filter_fills_for_equity(fills):
        sym = f.get("symbol")
        action = f.get("action")
        ts = f.get("timestamp")
        try:
            qty = float(f.get("qty") or 0)
            px = float(f.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if not sym or not ts or qty <= 0 or px <= 0:
            continue
        if f.get("cash_after") is not None:
            try:
                last_cash = float(f.get("cash_after"))
            except (TypeError, ValueError):
                pass
        if ts in by_t:
            continue
        by_t[ts] = {
            "t": ts,
            "equity": round(float(_equity_at_or_before(stored_marks, ts, start_eq)), 2),
            "cash": round(last_cash, 2),
            "event": f"{action} {sym}",
        }

    # Current snapshot mark (true M2M) — must always be present when known.
    now_eq = snap.get("equity")
    now_ts = snap.get("updated_at") or datetime.now(timezone.utc).isoformat()
    if now_eq is not None:
        cur = {
            "t": now_ts,
            "equity": round(float(now_eq), 2),
            "cash": round(float(snap.get("cash") if snap.get("cash") is not None else last_cash), 2),
            "event": "mark",
        }
        by_t[now_ts] = cur
        try:
            prev = stored_rows
            bucket = str(cur["t"])[:16]
            last_t = ""
            if prev:
                last_t = str(prev[-1].get("t") or prev[-1].get("timestamp") or "")
            if not prev or last_t[:16] != bucket:
                with curve_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(cur) + "\n")
        except Exception:
            pass

    points = [by_t[k] for k in sorted(by_t.keys())]

    # downsample if huge
    if len(points) > 400:
        step = max(1, len(points) // 400)
        head = points[::step]
        if head[-1] is not points[-1]:
            head.append(points[-1])
        points = head

    end_eq = float(points[-1]["equity"]) if points else start_eq
    change = round(end_eq - start_eq, 2)
    change_pct = round((change / start_eq) * 100.0, 2) if start_eq else 0.0
    return {
        "currency": "USD",
        "start_equity": start_eq,
        "latest_equity": end_eq,
        "change": change,
        "change_pct": change_pct,
        "points": points,
        "source": "local_paper_fills_and_marks",
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
    latest_consensus_row = None
    for row in reversed(today_consensus or consensus):
        if row.get("model1"):
            latest_decision = row.get("model1")
            latest_consensus_row = row
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
        m1 = row.get("model1") or {}
        m2 = row.get("model2") or {}
        sym = m1.get("symbol", "N/A")
        action = str(m1.get("action") or "").upper()
        validation = row.get("validation") or {}
        agreed = bool(row.get("consensus"))
        valid = validation.get("valid")
        reason = (
            validation.get("reason")
            or row.get("reason")
            or m1.get("thesis")
            or ""
        )

        # Label honestly: HOLD focus mismatches are not risk blocks
        if action == "HOLD" and agreed and (valid is None or valid is True):
            status = "hold"
            headline = "HOLD — no new trade"
        elif action == "HOLD" and not agreed:
            status = "hold"
            headline = "HOLD — desks disagreed on focus"
            reason = row.get("reason") or reason
        elif agreed and valid:
            status = "validated"
            headline = f"{action} {sym} — validated"
        elif agreed and valid is False:
            status = "blocked"
            headline = f"{action} {sym} — blocked"
        else:
            status = "no_consensus"
            headline = f"{action} {sym} — no consensus"
            reason = row.get("reason") or reason

        if (m1.get("symbol") or "") != (m2.get("symbol") or "") or str(m1.get("action") or "") != str(m2.get("action") or ""):
            m3 = row.get("model3") or {}
            desk = (
                f"Desks: {m1.get('action')} {m1.get('symbol')} ({m1.get('confidence')}%) vs "
                f"{m2.get('action')} {m2.get('symbol')} ({m2.get('confidence')}%)"
            )
            if m3:
                desk += (
                    f" vs {m3.get('action')} {m3.get('symbol')} ({m3.get('confidence')}%)"
                )
            desk += ". "
            reason = desk + (reason or "")

        # Collect every agent's individual vote (juniors + seniors)
        agents = []
        for key in ("junior1", "junior2", "junior3", "junior4", "model1", "model2", "model3"):
            a = row.get(key) or {}
            if not a:
                continue
            a_action = str(a.get("action") or "").upper()
            a_sym = a.get("symbol") or "CASH"
            agents.append(
                {
                    "model": a.get("model") or key,
                    "action": a_action,
                    "symbol": a_sym,
                    "confidence": a.get("confidence"),
                    "source": a.get("source"),
                    "provider": a.get("provider"),
                    "tier": "junior" if key.startswith("junior") else "senior",
                }
            )

        activity.append(
            {
                "type": "consensus",
                "symbol": sym,
                "status": status,
                "headline": headline,
                "detail": reason,
                "agents": agents,
                "tier": row.get("tier"),
                "escalate_reason": row.get("escalate_reason"),
                "stage": row.get("stage"),
                "junior_nomination": row.get("junior_nomination"),
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

    # Closed results newest first (Farzad-style list).
    # Apply the shared real-run cutoff (MIN_FILL_DATE, matching the weekly report)
    # here at the SOURCE so every downstream view — closed_recent, Top 5 Take
    # Profit, Top 5 Stop Loss — excludes early test/validation trades.
    closed_sorted = sorted(
        closed,
        key=lambda r: r.get("timestamp") or "",
        reverse=True,
    )
    closed_results = []
    for row in closed_sorted:
        if row.get("symbol") in ("AAA",):
            continue
        if str(row.get("timestamp") or "")[:10] < MIN_FILL_DATE:
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

    # Top take-profit / best locked winners (for quick success scan)
    def _is_tp_reason(reason: str, thesis: str = "") -> bool:
        blob = f"{reason or ''} {thesis or ''}".lower()
        keys = (
            "take_profit", "take-profit", "take profit", "target", "tp",
            "demo_take_profit", "test_tp",
        )
        return any(k in blob for k in keys)

    junk_syms = {"AAA", "TEST", "DUMMY"}
    winners = []
    for row in closed_results:
        sym = str(row.get("symbol") or "").upper()
        if not sym or sym in junk_syms:
            continue
        pnl_f = row.get("realized_pnl")
        try:
            pnl_f = float(pnl_f) if pnl_f is not None else None
        except Exception:
            pnl_f = None
        if pnl_f is None or pnl_f <= 0:
            continue
        avg = float(row.get("avg_cost") or 0)
        qty = float(row.get("qty") or 0)
        exit_px = float(row.get("exit_price") or 0)
        entry_notional = round(avg * qty, 2) if avg and qty else None
        exit_notional = row.get("proceeds")
        if exit_notional is None and exit_px and qty:
            exit_notional = round(exit_px * qty, 2)
        reason = str(row.get("reason") or "")
        thesis = str(row.get("thesis") or "")
        tp_hit = _is_tp_reason(reason, thesis)
        winners.append(
            {
                "symbol": sym,
                "timestamp": row.get("timestamp"),
                "opened_at": row.get("opened_at"),
                "qty": qty,
                "entry_price": avg,
                "exit_price": exit_px or None,
                "entry_value": entry_notional,
                "exit_value": float(exit_notional) if exit_notional is not None else None,
                "realized_pnl": round(pnl_f, 2),
                "realized_pnl_pct": row.get("realized_pnl_pct"),
                "reason": reason or ("take_profit" if tp_hit else "winner"),
                "take_profit_hit": tp_hit,
                "hold_seconds": row.get("hold_seconds"),
            }
        )

    # Prefer explicit take-profit winners first, then largest $ P/L
    winners.sort(
        key=lambda r: (
            0 if r.get("take_profit_hit") else 1,
            -float(r.get("realized_pnl") or 0),
            str(r.get("timestamp") or ""),
        )
    )
    # de-dupe by symbol keeping best pnl row
    seen = set()
    top_take_profits = []
    for w in winners:
        sym = w["symbol"]
        if sym in seen:
            continue
        seen.add(sym)
        top_take_profits.append(w)
        if len(top_take_profits) >= 5:
            break



    # Top stop-loss / worst locked losers (pair with Top 5 Take Profit)
    def _is_sl_reason(reason: str, thesis: str = "") -> bool:
        blob = f"{reason or ''} {thesis or ''}".lower()
        keys = (
            "stop_loss", "stop-loss", "stop loss", "stopped", "hit stop", "sl",
            "demo_stop", "test_sl",
        )
        return any(k in blob for k in keys)

    losers = []
    for row in closed_results:
        sym = str(row.get("symbol") or "").upper()
        if not sym or sym in junk_syms:
            continue
        pnl_f = row.get("realized_pnl")
        try:
            pnl_f = float(pnl_f) if pnl_f is not None else None
        except Exception:
            pnl_f = None
        if pnl_f is None or pnl_f >= 0:
            continue
        avg = float(row.get("avg_cost") or 0)
        qty = float(row.get("qty") or 0)
        exit_px = float(row.get("exit_price") or 0)
        entry_notional = round(avg * qty, 2) if avg and qty else None
        exit_notional = row.get("proceeds")
        if exit_notional is None and exit_px and qty:
            exit_notional = round(exit_px * qty, 2)
        reason = str(row.get("reason") or "")
        thesis = str(row.get("thesis") or "")
        sl_hit = _is_sl_reason(reason, thesis)
        losers.append(
            {
                "symbol": sym,
                "timestamp": row.get("timestamp"),
                "opened_at": row.get("opened_at"),
                "qty": qty,
                "entry_price": avg,
                "exit_price": exit_px or None,
                "entry_value": entry_notional,
                "exit_value": float(exit_notional) if exit_notional is not None else None,
                "realized_pnl": round(pnl_f, 2),
                "realized_pnl_pct": row.get("realized_pnl_pct"),
                "reason": reason or ("stop_loss" if sl_hit else "loser"),
                "stop_loss_hit": sl_hit,
                "hold_seconds": row.get("hold_seconds"),
            }
        )

    # Prefer explicit stop-loss tags first, then worst (most negative) $ P/L
    losers.sort(
        key=lambda r: (
            0 if r.get("stop_loss_hit") else 1,
            float(r.get("realized_pnl") or 0),  # more negative first
            str(r.get("timestamp") or ""),
        )
    )
    seen_l = set()
    top_stop_losses = []
    for w in losers:
        sym = w["symbol"]
        if sym in seen_l:
            continue
        seen_l.add(sym)
        top_stop_losses.append(w)
        if len(top_stop_losses) >= 5:
            break

    thinking = build_thinking(holdings, latest_decision, latest_candidate, snap, now, cfg, latest_consensus_row=latest_consensus_row)
    equity_curve = build_equity_curve(fills, snap, starting)
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
        "top_take_profits": top_take_profits,
        "top_stop_losses": top_stop_losses,
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
            "junior_enabled": bool(cfg.get("consensus_rules", {}).get("junior_enabled", True)),
            "junior_model_1": cfg.get("consensus_rules", {}).get("junior_model_1", "grok-4.3"),
            "junior_model_1_fallback": cfg.get("consensus_rules", {}).get("junior_model_1_fallback", "grok-build-0.1"),
            "junior_model_2": cfg.get("consensus_rules", {}).get("junior_model_2", "claude-haiku-4-5"),
            "junior_model_3": cfg.get("consensus_rules", {}).get("junior_model_3", "deepseek-v4-flash"),
            "junior_models": cfg.get("consensus_rules", {}).get("junior_models", []),
            "junior_min_agree": cfg.get("consensus_rules", {}).get("junior_min_agree", 3),
            "junior_votes": ((today_consensus or consensus)[-1].get("junior_votes") if (today_consensus or consensus) else {}),
            "junior_model_3_enabled": bool(cfg.get("consensus_rules", {}).get("junior_model_3_enabled", True)),
            "model_1": cfg.get("consensus_rules", {}).get("model_1", "grok-4.5"),
            "model_1_effort": cfg.get("consensus_rules", {}).get("model_1_effort"),
            "model_2": cfg.get("consensus_rules", {}).get("model_2", "claude-sonnet-5"),
            "model_3": cfg.get("consensus_rules", {}).get("model_3", "claude-opus-5"),
            "model_3_effort": cfg.get("consensus_rules", {}).get("model_3_effort"),
            "sol_chart_validator_enabled": bool(cfg.get("consensus_rules", {}).get("sol_chart_validator_enabled", False)),
            "sol_model": cfg.get("consensus_rules", {}).get("sol_model", "gpt-5.6-sol"),
            "min_confidence": cfg.get("consensus_rules", {}).get("min_confidence", 70),
            "max_candidates_to_llm": cfg.get("consensus_rules", {}).get("max_candidates_to_llm", 8),
            "log_all_disagreements": bool(
                cfg.get("consensus_rules", {}).get("log_all_disagreements", True)
            ),
            "decision_mode": (
                "live_dual_llm"
                if any(
                    (r.get("model1") or {}).get("source") == "live"
                    or (r.get("model2") or {}).get("source") == "live"
                    or (r.get("junior1") or {}).get("source") == "live"
                    for r in (today_consensus or consensus)[-12:]
                )
                else "fallback_deterministic"
            ),
            "last_tier": ((today_consensus or consensus)[-1].get("tier") if (today_consensus or consensus) else None),
            "last_stage": ((today_consensus or consensus)[-1].get("stage") if (today_consensus or consensus) else None),
            "last_junior_nomination": ((today_consensus or consensus)[-1].get("junior_nomination") if (today_consensus or consensus) else None),
            "last_escalate_reason": (
                (today_consensus or consensus)[-1].get("escalate_reason")
                if (today_consensus or consensus)
                else None
            ),
            "live_share_recent": round(
                (
                    sum(
                        1
                        for r in (today_consensus or consensus)[-20:]
                        for side in ("model1", "model2", "model3", "junior1", "junior2", "junior3", "junior4")
                        if (r.get(side) or {}).get("source") == "live"
                    )
                    / max(
                        1,
                        sum(
                            1
                            for r in (today_consensus or consensus)[-20:]
                            for side in ("model1", "model2", "model3", "junior1", "junior2", "junior3", "junior4")
                            if r.get(side)
                        )
                        or 1,
                    )
                )
                * 100,
                1,
            ),
        },
        "learning": {
            "candidate_signals_logged": len(candidates),
            "fills_logged": len(fills),
            "closed_outcomes": len(closed),
            "consensus_events": len(consensus),
            "note": "Paper sample is provisional until large enough for durable edge review.",
        },
        "equity_curve": equity_curve,
        "system": {
            "broker": "local_paper_broker",
            "ibkr_mcp": "paused",
            "cadence": "every 15m during NYSE regular hours",
            "market_data": (market_data.provider_status() if market_data else {"provider": "yfinance"}),
            "note": "Simulated paper account with real market marks. Not IBKR-confirmed.",
        },
    }

    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(str(OUT))


if __name__ == "__main__":
    main()
