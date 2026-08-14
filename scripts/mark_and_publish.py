#!/usr/bin/env python3
"""
Lightweight NYSE mark-and-publish job (5m cadence).

- Market-hours gate only (unless --force)
- Quotes open holdings + SPY in ONE efficient bulk (IBKR gateway; yfinance fallback)
- mark_to_market on local paper broker
- regenerate dashboard-data.json
- git commit/push snapshot for Vercel

Does NOT run Alpha Radar or dual-LLM (saves model cost).
Quiet skip off-hours for no_agent cron.
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


def run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
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


def push_dashboard() -> str:
    files = ["dashboard-data.json"]
    # only push data; index rarely changes
    status = run(["git", "status", "--porcelain", "--"] + files)
    if not status.stdout.strip():
        return "dashboard unchanged (no git push)"

    add = run(["git", "add", "--"] + files)
    if add.returncode != 0:
        return f"git add failed: {(add.stderr or '')[:160]}"

    msg = f"mark refresh {datetime.now(NY).strftime('%Y-%m-%d %H:%M %Z')}"
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
    force = "--force" in sys.argv or os.environ.get("MARK_FORCE") == "1"
    no_push = "--no-push" in sys.argv or os.environ.get("MARK_NO_PUSH") == "1"
    now = datetime.now(NY)
    open_ok, reason = is_nyse_regular_session(now)

    if not open_ok and not force:
        # silent-ish for no_agent crons (empty-ish skip)
        print(f"SKIP marks — {reason}")
        return 0

    import market_data
    from paper_broker import load_broker

    broker = load_broker()
    state = broker.state
    positions = state.get("positions") or {}
    held = [s for s, p in positions.items() if float((p or {}).get("qty") or 0) > 0]
    symbols = list(dict.fromkeys(held + ["SPY"]))

    # Efficient multi-symbol path: IBKR official gateway primary, yfinance fallback.
    bulk = market_data.quotes_bulk(symbols, prefer="ibkr")
    prefetched = {
        s: float(row["price"])
        for s, row in (bulk or {}).items()
        if row and row.get("price") is not None
    }

    marks = broker.mark_to_market(symbols=held, prefetched=prefetched)
    snap = broker.snapshot(skip_mark=True)  # already marked above; avoid double-quote

    # optional: store spy mark on state for generator
    spy_px = prefetched.get("SPY")
    if spy_px is not None:
        from datetime import timezone as _tz
        state = broker.state
        state["spy_last"] = float(spy_px)
        state["spy_marked_at"] = datetime.now(_tz.utc).isoformat()
        broker._save()

    gen = run([sys.executable, str(SCRIPTS / "generate_dashboard_data.py")], timeout=180)
    if gen.returncode != 0:
        print("MARK GEN FAILED")
        print((gen.stderr or gen.stdout)[-400:])
        return 1

    # read summary
    dash_path = ROOT / "dashboard-data.json"
    equity = snap.get("equity")
    open_pnl = snap.get("open_pnl")
    try:
        dash = json.loads(dash_path.read_text(encoding="utf-8"))
        equity = (dash.get("summary") or {}).get("portfolio_value", equity)
        open_pnl = (dash.get("summary") or {}).get("open_pnl", open_pnl)
        md = (dash.get("system") or {}).get("market_data") or market_data.provider_status()
    except Exception:
        md = market_data.provider_status()

    push_msg = "push skipped"
    if not no_push and os.environ.get("PAPER_NO_PUSH") != "1":
        push_msg = push_dashboard()

    sources = sorted({(bulk.get(s) or {}).get("source") or "?" for s in held}) or ["?"]
    print(
        f"MARK OK — {now.strftime('%H:%M %Z')} | equity ${float(equity or 0):.2f} | "
        f"openP/L ${float(open_pnl or 0):.2f} | held {len(held)} | "
        f"quotes {','.join(sources)} | provider {md.get('provider','?')} | ibkr_gw {md.get('ibkr_gateway','?')} | "
        f"{push_msg}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"MARK ERROR: {e}")
        raise
