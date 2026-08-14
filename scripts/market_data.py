#!/usr/bin/env python3
"""
Market data provider layer.

Sources (primary -> fallback):
  - IBKR official gateway (delayed, read-only)  -> primary for holdings/portfolio
  - yfinance (bulk, free)                        -> fallback + scanner universe

FMP removed (2026-08) — IBKR official gateway is now the preferred quote source.
Strategy:
  - Single/holdings quotes: IBKR gateway first, yfinance fallback
  - Multi-symbol bulk (scanner universe): yfinance bulk (IBKR single-quote is slow for 28 symbols)
  - Never log API keys
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

from env_load import load_dotenv as _load_dotenv

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = float(os.environ.get("QUOTE_CACHE_TTL_SEC", "90"))

# Load .env after _CACHE_TTL resolution to preserve prior ordering/behavior.
_load_dotenv()


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _cache_get(key: str) -> Optional[dict]:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, row = hit
    if time.time() - ts <= _CACHE_TTL:
        return dict(row)
    return None


def _cache_set(key: str, row: dict) -> None:
    _CACHE[key] = (time.time(), dict(row))


def _ibkr_gateway_available() -> bool:
    """Best-effort check that the official gateway socket is reachable on IBKR_PORT."""
    port = int(os.environ.get("IBKR_PORT", "4001"))
    try:
        import socket
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return True
    except Exception:
        return False


def ibkr_quotes(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Query the official IBKR gateway for quotes (delayed, read-only)."""
    cleaned = [s.upper().strip() for s in symbols if s]
    out: Dict[str, Dict[str, Any]] = {}
    if not cleaned:
        return out
    if not _ibkr_gateway_available():
        return out
    script = _SCRIPT_DIR / "ibkr_quotes.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), ",".join(cleaned)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(_REPO_ROOT),
        )
        data = json.loads(proc.stdout.strip() or "{}")
        if not data.get("ok"):
            return out
        for q in data.get("quotes", []):
            sym = (q.get("symbol") or "").upper()
            price = _f(q.get("price"))
            if not sym or price is None:
                continue
            row = {
                "symbol": sym,
                "name": sym,
                "price": price,
                "bid": _f(q.get("bid")),
                "ask": _f(q.get("ask")),
                "previous_close": _f(q.get("close")),
                "percent_change": None,
                "volume": None,
                "avg_volume": None,
                "exchange": "",
                "source": "ibkr",
            }
            prev = row["previous_close"]
            if prev:
                row["percent_change"] = (price / prev - 1.0) * 100.0
            _cache_set(f"ibkr:{sym}", row)
            out[sym] = row
    except Exception:
        return out
    return out


def ibkr_quote(symbol: str) -> Optional[Dict[str, Any]]:
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    cached = _cache_get(f"ibkr:{sym}")
    if cached:
        return cached
    return ibkr_quotes([sym]).get(sym)


def yf_quote(symbol: str) -> Optional[Dict[str, Any]]:
    if yf is None:
        return None
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    cached = _cache_get(f"yf:{sym}")
    if cached:
        return cached
    try:
        t = yf.Ticker(sym)
        price = None
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            price = fi.get("lastPrice") or fi.get("last_price") or fi.get("regularMarketPrice")
        hist = t.history(period="10d")
        prev = None
        vol = None
        avg_vol = None
        if hist is not None and not hist.empty:
            if price is None:
                price = float(hist["Close"].iloc[-1])
            if len(hist) >= 2:
                prev = float(hist["Close"].iloc[-2])
            if "Volume" in hist:
                vol = float(hist["Volume"].iloc[-1])
                avg_vol = float(hist["Volume"].tail(10).mean())
        if price is None:
            return None
        pct = ((float(price) - prev) / prev * 100.0) if prev else None
        name = sym
        try:
            info = getattr(t, "info", None) or {}
            name = info.get("shortName") or info.get("longName") or sym
        except Exception:
            pass
        out = {
            "symbol": sym,
            "name": name,
            "price": float(price),
            "previous_close": float(prev) if prev is not None else None,
            "volume": float(vol) if vol is not None else None,
            "avg_volume": float(avg_vol) if avg_vol is not None else None,
            "percent_change": float(pct) if pct is not None else None,
            "exchange": "",
            "source": "yfinance",
        }
        _cache_set(f"yf:{sym}", out)
        return out
    except Exception:
        return None


def yf_quotes(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    cleaned = [s.upper().strip() for s in symbols if s]
    if not cleaned or yf is None:
        return out
    need = []
    for sym in cleaned:
        cached = _cache_get(f"yf:{sym}")
        if cached:
            out[sym] = cached
        else:
            need.append(sym)
    if not need:
        return out
    try:
        hist = yf.download(
            tickers=" ".join(need),
            period="10d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="ticker",
        )
        for sym in need:
            try:
                if len(need) == 1:
                    df = hist
                else:
                    try:
                        df = hist[sym]
                    except Exception:
                        df = None
                if df is None or getattr(df, "empty", True):
                    q = yf_quote(sym)
                    if q:
                        out[sym] = q
                    continue
                close = df["Close"].dropna()
                if close.empty:
                    continue
                price = float(close.iloc[-1])
                prev = float(close.iloc[-2]) if len(close) >= 2 else None
                vol = None
                avg_vol = None
                if "Volume" in df and not df["Volume"].dropna().empty:
                    vol = float(df["Volume"].dropna().iloc[-1])
                    avg_vol = float(df["Volume"].tail(10).mean())
                pct = ((price - prev) / prev * 100.0) if prev else None
                row = {
                    "symbol": sym,
                    "name": sym,
                    "price": price,
                    "previous_close": prev,
                    "volume": vol,
                    "avg_volume": avg_vol,
                    "percent_change": pct,
                    "exchange": "",
                    "source": "yfinance",
                }
                _cache_set(f"yf:{sym}", row)
                out[sym] = row
            except Exception:
                q = yf_quote(sym)
                if q:
                    out[sym] = q
    except Exception:
        for sym in need:
            q = yf_quote(sym)
            if q:
                out[sym] = q
    return out


def provider_name() -> str:
    """Preferred single-quote provider for holdings."""
    pref = (os.environ.get("QUOTE_PROVIDER") or "ibkr").strip().lower()
    if pref in ("ibkr", "ibkr_gateway"):
        return "ibkr"
    if pref == "yfinance":
        return "yfinance"
    return "ibkr"


def quotes_bulk(
    symbols: List[str],
    prefer: str | None = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Efficient multi-symbol quotes.
    - `prefer=ibkr` (default for holdings): IBKR gateway first, yfinance fills missing
    - `prefer=yfinance` (scanner universe): yfinance bulk first, IBKR fills missing
    """
    cleaned = [s.upper().strip() for s in symbols if s]
    if not cleaned:
        return {}
    mode = (prefer or os.environ.get("BULK_QUOTE_PROVIDER") or "ibkr").strip().lower()

    out: Dict[str, Dict[str, Any]] = {}
    if mode == "yfinance":
        out = yf_quotes(cleaned)
        missing = [s for s in cleaned if s not in out]
        if missing:
            out.update(ibkr_quotes(missing))
        return out

    # default: ibkr primary
    out = ibkr_quotes(cleaned)
    missing = [s for s in cleaned if s not in out]
    if missing:
        out.update(yf_quotes(missing))
    return out


def quote_details(symbol: str) -> Optional[Dict[str, Any]]:
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    if provider_name() == "ibkr":
        q = ibkr_quote(sym)
        if q and q.get("price"):
            return q
        return yf_quote(sym)
    q = yf_quote(sym)
    if q:
        return q
    return ibkr_quote(sym)


def quote(symbol: str) -> Optional[float]:
    q = quote_details(symbol)
    if not q:
        return None
    try:
        return float(q["price"])
    except Exception:
        return None


def provider_status() -> Dict[str, Any]:
    return {
        "provider": provider_name(),
        "bulk_provider": (os.environ.get("BULK_QUOTE_PROVIDER") or "ibkr"),
        "ibkr_gateway": _ibkr_gateway_available(),
        "ibkr_port": os.environ.get("IBKR_PORT", "4001"),
        "yfinance": yf is not None,
        "quote_provider_env": os.environ.get("QUOTE_PROVIDER") or "",
        "cache_ttl_sec": _CACHE_TTL,
    }
