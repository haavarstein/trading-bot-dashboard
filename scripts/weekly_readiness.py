#!/usr/bin/env python3
"""
Weekly go-live readiness report for the trading bot.

Reads the paper-trading logs and reports progress against the go-live bar.
Designed for Hermes no_agent cron delivery (stdout -> Telegram when non-empty).
Run manually:  python weekly_readiness.py [--json]
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] if Path(__file__).resolve().name == "weekly_readiness.py" else Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Earliest close timestamp (UTC date) included in readiness stats. Trades closed
# before this (early test/validation days) are excluded so they don't skew
# win-rate / profit-factor. Set by operator; 2026-08-12 = start of the real run.
MIN_CLOSE_DATE = "2026-08-12"

# The cron runs a copy under ~/AppData/Local/hermes/scripts/, where parents[1]
# resolves to the hermes home (its ./data is empty/absent) instead of the
# trading-bot repo. Always prefer the real paper-trading data dir so the weekly
# report reflects paper activity, not zeros.
_REPO_DATA = Path.home() / "trading-bot" / "data"
if _REPO_DATA.is_dir() and (_REPO_DATA / "closed_trades.jsonl").exists():
    DATA = _REPO_DATA
GO_LIVE = {
    "win_rate_min": 35.0,
    "profit_factor_min": 1.5,
    "max_drawdown_max_pct": 3.0,
    "min_trades": 50,
    "min_days": 28,  # ~4 weeks
    "stable_bot": True,
}


def _read_jsonl(path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def _read_json(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}


def _equity_curve():
    vals = []
    for l in _read_jsonl(DATA / "equity_curve.jsonl"):
        eq = l.get("equity") or l.get("value")
        if eq:
            # dashboard writer uses "t" (also accept "timestamp" for safety)
            ts = l.get("t") or l.get("timestamp") or ""
            vals.append((ts, float(eq)))
    return vals


def _max_drawdown(vals):
    # Empty curve → None (missing, not 0.0): must NOT count as a pass.
    if not vals:
        return None, ""
    peak = vals[0][1]
    mdd, mdd_at = 0.0, ""
    for ts, v in vals:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak else 0
        if dd > mdd:
            mdd, mdd_at = dd, ts
    return mdd, mdd_at


def build_report():
    rows = _read_jsonl(DATA / "closed_trades.jsonl")
    # exclude test/demo artifacts and trades closed before the real-run start
    real = [r for r in rows
            if "test" not in str(r.get("reason", "")).lower()
            and "demo" not in str(r.get("reason", "")).lower()
            and str(r.get("timestamp", ""))[:10] >= MIN_CLOSE_DATE]
    wins = [r for r in real if r.get("realized_pnl", 0) > 0]
    losses = [r for r in real if r.get("realized_pnl", 0) <= 0]
    n = len(real)
    wr = len(wins) / n * 100 if n else 0.0
    gross_win = sum(r.get("realized_pnl", 0) for r in wins)
    gross_loss = abs(sum(r.get("realized_pnl", 0) for r in losses))
    pf = gross_win / gross_loss if gross_loss else float("inf")
    net = gross_win - gross_loss
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0

    curve = _equity_curve()
    mdd, mdd_at = _max_drawdown(curve)

    # time span = bot's actual operating window (equity curve), NOT the span of
    # the filtered trade list. The cutoff drops early test trades; trading-days
    # is a stability gate and must reflect how long the bot has been running.
    span_days = 0
    if curve:
        try:
            ts = [t for t, _ in curve if t]
            if ts:
                d0 = datetime.fromisoformat(min(ts).replace("Z", "+00:00"))
                d1 = datetime.fromisoformat(max(ts).replace("Z", "+00:00"))
                span_days = max(1, int((d1 - d0).days))
        except Exception:
            span_days = 0

    # benchmark
    bench = _read_json(DATA / "benchmark.json") if (DATA / "benchmark.json").exists() else {}
    spy = bench.get("return_pct")
    summary = _read_json(ROOT / "dashboard-data.json") or _read_json(DATA / "dashboard-data.json")
    total_ret = None
    vs_spy = None
    if summary:
        s = summary.get("summary") or summary
        total_ret = s.get("total_return_pct")
        vs_spy = s.get("return_vs_spy") or s.get("vs_spy_pct")
        if vs_spy is None and total_ret is not None and spy is not None:
            vs_spy = total_ret - spy

    # readiness. stable_bot = operator declares the bot frozen (marker file exists):
    # once no more consensus/logic changes are being shipped, create data/.BOT_FROZEN.
    checks = {
        "trades": n >= GO_LIVE["min_trades"],
        "days": span_days >= GO_LIVE["min_days"],
        "win_rate": wr >= GO_LIVE["win_rate_min"],
        "profit_factor": pf >= GO_LIVE["profit_factor_min"],
        "drawdown": mdd is not None and mdd <= GO_LIVE["max_drawdown_max_pct"],
        "stable_bot": (DATA / ".BOT_FROZEN").exists(),
    }
    passed = [k for k, v in checks.items() if v]
    ready = len(passed) == len(checks)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "READY" if ready else "NOT_READY",
        "checks_passed": f"{len(passed)}/{len(checks)}",
        "checks": checks,
        "go_live_bar": GO_LIVE,
        "metrics": {
            "closed_trades": n,
            "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2) if pf != float("inf") else None,
            "net_realized": round(net, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_drawdown_pct": round(mdd, 2) if mdd is not None else None,
            "total_return_pct": total_ret,
            "vs_spy_pct": vs_spy,
            "trading_days": span_days,
            "fills_logged": len(_read_jsonl(DATA / "fills.jsonl")),
        },
    }
    return report


def _pct(v):
    """Format a percent value as 'X.XX%'; return 'n/a' (no '%') when missing/absent."""
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.2f}%"
    except Exception:
        return "n/a"


def format_text(r):
    m = r["metrics"]
    c = r["checks"]
    mdd = "n/a" if m["max_drawdown_pct"] is None else _pct(m["max_drawdown_pct"])
    lines = []
    lines.append("📊 *TRADING READINESS REPORT*")
    lines.append(f"Verdict: **{'✅ READY' if r['verdict']=='READY' else '🔴 NOT READY'}** ({r['checks_passed']} gates passed)")
    lines.append("")
    lines.append(f"· Trades: {m['closed_trades']} (need {r['go_live_bar']['min_trades']}) {'✅' if c['trades'] else '❌'}")
    lines.append(f"· Win rate: {m['win_rate']}% (need {r['go_live_bar']['win_rate_min']}%) {'✅' if c['win_rate'] else '❌'}")
    lines.append(f"· Profit factor: {m['profit_factor']} (need {r['go_live_bar']['profit_factor_min']}) {'✅' if c['profit_factor'] else '❌'}")
    lines.append(f"· Max drawdown: {mdd} (cap {r['go_live_bar']['max_drawdown_max_pct']}%) {'✅' if c['drawdown'] else '❌'}")
    lines.append(f"· Trading days: {m['trading_days']} (need {r['go_live_bar']['min_days']}) {'✅' if c['days'] else '❌'}")
    lines.append(f"· Bot stable/frozen: {'✅' if c['stable_bot'] else '❌'} (create data/.BOT_FROZEN once no more logic changes)")
    lines.append("")
    lines.append(f"Net realized: ${m['net_realized']} | avg win ${m['avg_win']} / avg loss ${m['avg_loss']}")
    lines.append(f"Total return: {_pct(m['total_return_pct'])} | vs SPY: {_pct(m['vs_spy_pct'])}")
    lines.append(f"Fills logged: {m['fills_logged']} | generated {r['generated_at'][:16]}Z")
    if r["verdict"] != "READY":
        missing = [k for k, v in c.items() if not v]
        lines.append("")
        lines.append("Missing: " + ", ".join(missing))
    lines.append("")
    lines.append("Dashboard: https://trading-bot-delta-roan.vercel.app")
    return "\n".join(lines)


def main():
    r = build_report()
    if "--json" in sys.argv:
        print(json.dumps(r, indent=2, default=str))
    else:
        print(format_text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
