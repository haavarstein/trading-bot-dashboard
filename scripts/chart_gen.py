#!/usr/bin/env python3
"""
Chart generator for the GPT-5.6 Sol chart-vision senior.

Fetches daily OHLCV for a symbol (yfinance) and renders a candlestick chart
(with optional support/resistance grid) to a PNG that Sol can read.

Usage:
  python chart_gen.py TSLA                      -> data/charts/TSLA.png
  python chart_gen.py TSLA --days 60             -> 60 trading days
  python chart_gen.py TSLA --out custom.png

Designed to be called by the autotrader before the Sol vision call.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHART_DIR = ROOT / "data" / "charts"


def fetch_ohlcv(symbol: str, days: int = 60) -> "object":
    import yfinance as yf

    df = yf.download(symbol, period=f"{days}d", interval="1d", progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"no price data for {symbol}")
    # Flatten MultiIndex columns if present
    if isinstance(df.columns, type(df.columns)) and getattr(df.columns, "nlevels", 1) > 1:
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.rename(columns={"Open": "Open", "High": "High", "Low": "Low", "Close": "Close", "Volume": "Volume"})
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    return df


def add_support_resistance(df) -> tuple[list[float], list[float]]:
    """Naive S/R: recent swing highs/lows from pivot detection."""
    highs = list(df["High"])
    lows = list(df["Low"])
    supports: list[float] = []
    resistances: list[float] = []
    n = len(highs)
    for i in range(1, n - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            resistances.append(round(float(highs[i]), 2))
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            supports.append(round(float(lows[i]), 2))
    # dedupe nearby levels
    def dedupe(vals: list[float], tol: float = 0.01) -> list[float]:
        out: list[float] = []
        for v in sorted(set(vals)):
            if not out or abs(v - out[-1]) / out[-1] > tol:
                out.append(v)
        return out[:6]

    return dedupe(supports), dedupe(resistances)


def render_chart(df, symbol: str, out_path: Path, supports=None, resistances=None) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import mplfinance as mpf

    out_path.parent.mkdir(parents=True, exist_ok=True)
    supports = supports or []
    resistances = resistances or []
    # hlines: dict of price -> color/label
    hlines = {}
    hlabels = {}
    for s in supports:
        hlines[s] = "g"
        hlabels[s] = f"S {s}"
    for r in resistances:
        hlines[r] = "r"
        hlabels[r] = f"R {r}"

    mc = mpf.make_marketcolors(up="g", down="r", edge="inherit", wick="inherit", volume="inherit")
    style = mpf.make_mpf_style(marketcolors=mc, gridstyle=":", figcolor="#111111", facecolor="#111111",
                               edgecolor="#333333", gridcolor="#2a2a2a")
    try:
        ap = [mpf.make_addplot([], type="line")]  # placeholder to avoid None hlines bug
        mpf.plot(
            df, type="candle", style=style, title=f"\n{symbol}  (daily)",
            ylabel="Price", volume=True, figsize=(12, 7), savefig=dict(fname=str(out_path), dpi=110),
            hlines=dict(hlines=hlines, colors=[hlines[k] for k in hlines], linestyle="--",
                        linewidths=1.2, alpha=0.7),
            axtitle=f"Supports: {supports}  Resistances: {resistances}",
            panel_ratios=(4, 1), tight_layout=True,
        )
    except TypeError:
        # fallback without hlines kwargs if API differs
        mpf.plot(
            df, type="candle", style=style, title=f"\n{symbol}  (daily)",
            ylabel="Price", volume=True, figsize=(12, 7), savefig=dict(fname=str(out_path), dpi=110),
            panel_ratios=(4, 1), tight_layout=True,
        )
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    symbol = args.symbol.upper().strip()
    try:
        df = fetch_ohlcv(symbol, args.days)
    except Exception as e:
        print(f"chart_gen ERROR: {e}", file=sys.stderr)
        return 1
    supports, resistances = add_support_resistance(df)
    out = Path(args.out) if args.out else (CHART_DIR / f"{symbol}.png")
    try:
        render_chart(df, symbol, out, supports, resistances)
    except Exception as e:
        print(f"render ERROR: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
