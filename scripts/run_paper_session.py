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
NY = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


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
        # Quiet-ish skip for off-hours cron ticks that still fire near edges
        print(f"SKIP paper session — {reason}")
        return 0

    py = sys.executable
    print(f"PAPER SESSION START — {now.strftime('%Y-%m-%d %H:%M %Z')} ({reason if open_ok else 'FORCED'})")

    # 1) Scan
    scan = run([py, str(SCRIPTS / "alpha_radar.py")], timeout=300)
    if scan.returncode != 0:
        print("SCAN FAILED")
        print((scan.stderr or scan.stdout)[-500:])
        return 1
    print("SCAN OK")

    # 2) Trade cycle (simulated paper)
    trade = run([py, str(SCRIPTS / "autotrader.py")], timeout=180)
    trade_out = (trade.stdout or "") + (trade.stderr or "")
    if trade.returncode != 0:
        print("TRADE CYCLE FAILED")
        print(trade_out[-500:])
        # still refresh dashboard from whatever logs exist
    else:
        print("TRADE CYCLE OK")

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

    open_syms = []
    try:
        pf = json.loads((ROOT / "data" / "portfolio.json").read_text(encoding="utf-8"))
        open_syms = sorted((pf.get("positions") or {}).keys())
        cash = pf.get("cash")
    except Exception:
        cash = None

    # 3) Dashboard data
    dash = run([py, str(SCRIPTS / "generate_dashboard_data.py")], timeout=60)
    if dash.returncode != 0:
        print("DASHBOARD GEN FAILED")
        print((dash.stderr or dash.stdout)[-400:])
        return 1
    print("DASHBOARD DATA OK")

    # 4) Publish snapshot (best-effort)
    publish = "skip publish"
    if os.environ.get("PAPER_NO_PUSH") == "1":
        publish = "publish disabled (PAPER_NO_PUSH=1)"
    else:
        try:
            publish = push_dashboard()
        except Exception as exc:
            publish = f"publish error: {exc}"

    print("---")
    print(f"top_candidate: {cand_sym or 'n/a'}")
    if order_action == "HOLD":
        print(f"decision: HOLD {order_sym or ''} — no new fill".strip())
        print(f"why: {(last_order.get('thesis') or order_reason or 'no actionable edge')[:140]}")
    elif order_action in ("BUY", "SELL") and order_status == "FILLED_PAPER":
        px = last_order.get("entry_price")
        qty = last_order.get("qty")
        print(f"fill: {order_action} {order_sym} qty={qty} @ ${float(px or 0):.2f} -> {order_status}")
        if order_reason:
            print(f"reason: {str(order_reason)[:120]}")
    else:
        print(f"latest_order: {order_action or 'n/a'} {order_sym or 'n/a'} ({order_status or 'n/a'})")
    if last_fill:
        print(
            f"last_fill: {last_fill.get('action')} {last_fill.get('symbol')} @ "
            f"${float(last_fill.get('price') or 0):.2f} ({str(last_fill.get('timestamp') or '')[:19]}Z)"
        )
    print(f"open_positions: {', '.join(open_syms) if open_syms else 'none'}" + (f" | cash=${float(cash):.2f}" if cash is not None else ""))
    if selected:
        print(f"signal: {selected}")
    print(f"publish: {publish}")
    print("dashboard: https://trading-bot-delta-roan.vercel.app")
    print("PAPER SESSION DONE")
    return 0 if trade.returncode == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
