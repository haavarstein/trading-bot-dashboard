#!/usr/bin/env python3
"""
Simulated paper-trading session runner (no IBKR MCP required).

Flow:
  1) NYSE session gate (America/New_York, regular hours)
  2) Alpha Radar candidate scan
  3) Autotrader consensus + simulated order
  4) Regenerate public dashboard-data.json
  5) Optionally git commit/push dashboard snapshot for Vercel

Designed for Hermes cron (no_agent friendly): prints a short summary to stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
NY = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
DASHBOARD_URL = os.environ.get(
    "DASHBOARD_URL", "https://trading-bot-delta-roan.vercel.app"
)


def log(msg: str = "") -> None:
    """Diagnostics for local/cron logs — NOT delivered to Telegram (no_agent uses stdout)."""
    print(msg, file=sys.stderr, flush=True)


def tg(msg: str) -> None:
    """Telegram-facing stdout for Hermes no_agent delivery."""
    print(msg, flush=True)



def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONUTF8": "1"},
    )


def is_nyse_regular_session(now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(NY)
    if now.weekday() >= 5:
        return False, f"weekend ({now.strftime('%A')})"
    t = now.timetz().replace(tzinfo=None)
    if t < MARKET_OPEN:
        return False, f"before open ({now.strftime('%H:%M %Z')})"
    if t >= MARKET_CLOSE:
        return False, f"after close ({now.strftime('%H:%M %Z')})"
    return True, f"regular session ({now.strftime('%H:%M %Z')})"


def last_jsonl_row(path: Path) -> dict | None:
    if not path.exists():
        return None
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except Exception:
        return None


def last_jsonl_symbol(path: Path) -> str | None:
    row = last_jsonl_row(path)
    if not row:
        return None
    if "symbol" in row:
        return row.get("symbol")
    return (row.get("model1") or {}).get("symbol")


def push_dashboard() -> str:
    """Commit only public dashboard snapshot files and push main."""
    # Never stage secrets
    files = ["dashboard-data.json", "index.html"]
    status = run(["git", "status", "--porcelain", "--"] + files)
    if not status.stdout.strip():
        return "dashboard unchanged (no git push)"

    add = run(["git", "add", "--"] + files)
    if add.returncode != 0:
        return f"git add failed: {add.stderr.strip()[:160]}"

    msg = f"paper session dashboard {datetime.now(NY).strftime('%Y-%m-%d %H:%M %Z')}"
    commit = run(["git", "commit", "-m", msg])
    if commit.returncode != 0:
        out = (commit.stdout or "") + (commit.stderr or "")
        if "nothing to commit" in out.lower():
            return "dashboard unchanged (no git push)"
        return f"git commit failed: {out.strip()[:160]}"

    push = run(["git", "push", "origin", "main"], timeout=180)
    if push.returncode != 0:
        return f"git push failed: {(push.stderr or push.stdout)[:160]}"
    return "dashboard pushed to GitHub/Vercel"


def main() -> int:
    force = "--force" in sys.argv or os.environ.get("PAPER_FORCE") == "1"
    now = datetime.now(NY)
    open_ok, reason = is_nyse_regular_session(now)

    if not open_ok and not force:
        # Silent for Telegram (empty stdout). Details only on stderr.
        log(f"SKIP paper session — {reason}")
        return 0

    py = sys.executable
    log(f"PAPER SESSION START — {now.strftime('%Y-%m-%d %H:%M %Z')} ({reason if open_ok else 'FORCED'})")

    # 1) Scan
    scan = run([py, str(SCRIPTS / "alpha_radar.py")], timeout=300)
    if scan.returncode != 0:
        log("SCAN FAILED")
        err = (scan.stderr or scan.stdout)[-500:]
        log(err)
        try:
            from credit_alerts import looks_like_credit_error, notify_credit_issue
            if looks_like_credit_error(err):
                notify_credit_issue("alpha_radar", err)
        except Exception:
            pass
        return 1
    log("SCAN OK")

    # 2) Trade cycle (simulated paper)
    # Timeout must cover the influencer feed refresh (26 handles via Scrape
    # Creators ~130s) plus the LLM consensus (~60s). 180s was too tight.
    trade = run([py, str(SCRIPTS / "autotrader.py")], timeout=300)
    trade_out = (trade.stdout or "") + (trade.stderr or "")
    if trade.returncode != 0:
        log("TRADE CYCLE FAILED")
        log(trade_out[-500:])
        try:
            from credit_alerts import looks_like_credit_error, notify_credit_issue
            if looks_like_credit_error(trade_out):
                notify_credit_issue("autotrader", trade_out[-500:])
        except Exception:
            pass
        # still refresh dashboard from whatever logs exist
    else:
        log("TRADE CYCLE OK")

    # Extract a compact signal for the summary
    selected = None
    for line in trade_out.splitlines():
        # Prefer actual fill lines over generic VALIDATION PASSED (which also fires on HOLD)
        if "[PAPER] BUY" in line or "[PAPER] SELL" in line or "FILLED_PAPER" in line:
            selected = line.strip()
        elif selected is None and ("CONSENSUS REACHED" in line or "VALIDATION PASSED" in line or "HOLD" in line):
            selected = line.strip()

    last_order = last_jsonl_row(ROOT / "data" / "order_ledger.jsonl") or {}
    last_fill = last_jsonl_row(ROOT / "data" / "fills.jsonl") or {}
    order_action = last_order.get("action")
    order_sym = last_order.get("symbol")
    order_status = last_order.get("status")
    order_reason = last_order.get("reason_code") or last_order.get("thesis") or ""

    cand_sym = None
    cand_path = ROOT / "data" / "candidates.json"
    if cand_path.exists():
        try:
            blob = json.loads(cand_path.read_text(encoding="utf-8"))
            cands = blob.get("candidates") or []
            if cands:
                cand_sym = cands[0].get("symbol")
        except Exception:
            pass

    # Fresh mark-to-market snapshot for Telegram bullets (same numbers style as dashboard)
    open_syms = []
    holdings_rows = []
    cash = None
    equity = None
    open_pnl_total = None
    try:
        from paper_broker import load_broker

        cfg = {}
        cfg_path = ROOT / "config" / "autonomy_config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        starting = float(cfg.get("account", {}).get("starting_capital", 1000))
        snap = load_broker(starting_cash=starting).snapshot()
        cash = snap.get("cash")
        equity = snap.get("equity")
        open_pnl_total = snap.get("open_pnl")
        if open_pnl_total is None:
            open_pnl_total = snap.get("unrealized_pnl")
        holdings_rows = []
        for h in list(snap.get("positions") or []):
            # Normalize broker snapshot keys to dashboard-style names.
            holdings_rows.append(
                {
                    "symbol": h.get("symbol"),
                    "qty": h.get("qty"),
                    "avg_cost": h.get("avg_cost"),
                    "last": h.get("last", h.get("current_price", h.get("last_price"))),
                    "open_pnl": h.get("open_pnl", h.get("unrealized_pnl")),
                    "open_pnl_pct": h.get("open_pnl_pct"),
                    "stop": h.get("stop", h.get("stop_loss")),
                    "target": h.get("target", h.get("take_profit")),
                }
            )
        # largest absolute move first, then symbol
        holdings_rows.sort(key=lambda h: (-abs(float(h.get("open_pnl") or 0)), str(h.get("symbol") or "")))
        open_syms = [h.get("symbol") for h in holdings_rows if h.get("symbol")]
    except Exception:
        try:
            pf = json.loads((ROOT / "data" / "portfolio.json").read_text(encoding="utf-8"))
            open_syms = sorted((pf.get("positions") or {}).keys())
            cash = pf.get("cash")
            for sym, pos in (pf.get("positions") or {}).items():
                holdings_rows.append(
                    {
                        "symbol": sym,
                        "qty": pos.get("qty"),
                        "avg_cost": pos.get("avg_cost"),
                        "last_price": pos.get("last_price") or pos.get("last"),
                        "open_pnl": pos.get("open_pnl"),
                        "open_pnl_pct": pos.get("open_pnl_pct"),
                        "stop_loss": pos.get("stop_loss"),
                        "take_profit": pos.get("take_profit"),
                    }
                )
        except Exception:
            cash = None

    # 3) Dashboard data
    dash = run([py, str(SCRIPTS / "generate_dashboard_data.py")], timeout=60)
    if dash.returncode != 0:
        log("DASHBOARD GEN FAILED")
        log((dash.stderr or dash.stdout)[-400:])
        return 1
    log("DASHBOARD DATA OK")

    # 4) Publish snapshot (best-effort)
    publish = "skip publish"
    if os.environ.get("PAPER_NO_PUSH") == "1":
        publish = "publish disabled (PAPER_NO_PUSH=1)"
    else:
        try:
            publish = push_dashboard()
        except Exception as exc:
            publish = f"publish error: {exc}"

    # Build holdings lines (stderr always; Telegram only on BUY/SELL)
    hold_lines = []
    if holdings_rows:
        for h in holdings_rows:
            sym = h.get("symbol") or "?"
            qty = h.get("qty")
            last = h.get("last") if h.get("last") is not None else h.get("last_price")
            avg = h.get("avg_cost")
            pnl = float(h.get("open_pnl") or 0)
            pct = h.get("open_pnl_pct")
            if pct is None and avg and last and float(avg) != 0:
                pct = (float(last) - float(avg)) / float(avg) * 100.0
            pct = float(pct or 0)
            stop = h.get("stop") if h.get("stop") is not None else h.get("stop_loss")
            target = h.get("target") if h.get("target") is not None else h.get("take_profit")
            sign = "+" if pnl >= 0 else ""
            qty_s = f"{float(qty):.4f}".rstrip("0").rstrip(".") if qty is not None else "?"
            last_s = f"${float(last):.2f}" if last is not None else "n/a"
            stop_s = f"{float(stop):.2f}" if stop is not None else "--"
            tgt_s = f"{float(target):.2f}" if target is not None else "--"
            hold_lines.append(
                f"• {sym} {qty_s}sh  last {last_s}  {sign}${pnl:.2f} ({sign}{pct:.2f}%)  "
                f"stop {stop_s} / tgt {tgt_s}"
            )

    log("---")
    log(f"top_candidate: {cand_sym or 'n/a'}")
    is_fill = order_action in ("BUY", "SELL") and str(order_status) == "FILLED_PAPER"
    if order_action == "HOLD":
        log(f"decision: HOLD {order_sym or ''} — no new fill".strip())
        log(f"why: {(last_order.get('thesis') or order_reason or 'no actionable edge')[:140]}")
    elif is_fill:
        px = last_order.get("entry_price")
        qty = last_order.get("qty")
        log(f"fill: {order_action} {order_sym} qty={qty} @ ${float(px or 0):.2f} -> {order_status}")
        if order_reason:
            log(f"reason: {str(order_reason)[:120]}")
    else:
        log(f"latest_order: {order_action or 'n/a'} {order_sym or 'n/a'} ({order_status or 'n/a'})")
    if last_fill:
        log(
            f"last_fill: {last_fill.get('action')} {last_fill.get('symbol')} @ "
            f"${float(last_fill.get('price') or 0):.2f} ({str(last_fill.get('timestamp') or '')[:19]}Z)"
        )
    if equity is not None:
        try:
            log(f"equity: ${float(equity):.2f} | cash: ${float(cash or 0):.2f} | open_pnl: ${float(open_pnl_total or 0):+.2f}")
        except Exception:
            pass
    if hold_lines:
        log("holdings:")
        for line in hold_lines:
            log(line)
    else:
        log("holdings: none")
    log(
        f"open_positions: {', '.join(open_syms) if open_syms else 'none'}"
        + (f" | cash=${float(cash):.2f}" if cash is not None else "")
    )
    if selected:
        log(f"signal: {selected}")
    log(f"publish: {publish}")
    log(f"dashboard: {DASHBOARD_URL}")
    log("PAPER SESSION DONE")

    # Telegram: the trade signal (PAPER BUY/SELL with risk details) is already sent
    # by autotrader.execute_trade -> notify_trade_signal. Nothing else is sent here
    # so the user receives exactly ONE message per fill, not three. Keep diagnostics
    # in the stderr log (not delivered by the no_agent cron).
    if is_fill:
        log(f"tg-fill (single notification via autotrader): {order_action} {order_sym} qty={qty}")

    return 0 if trade.returncode == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
