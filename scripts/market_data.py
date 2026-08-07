#!/usr/bin/env python3
"""
Market data provider layer.

Primary: Financial Modeling Prep (FMP) when FMP_API_KEY is set / QUOTE_PROVIDER=fmp
Fallback: yfinance

Never logs API keys.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None


def _load_dotenv() -> None:
    candidates = [
        Path.home() / "AppData" / "Local" / "hermes" / ".env",
        Path.home() / ".hermes" / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


_load_dotenv()

FMP_BASE = os.environ.get("FMP_BASE_URL", "https://financialmodelingprep.com").rstrip("/")


def provider_name() -> str:
    pref = (os.environ.get("QUOTE_PROVIDER") or "fmp").strip().lower()
    if pref == "fmp" and os.environ.get("FMP_API_KEY"):
        return "fmp"
    if pref == "yfinance":
        return "yfinance"
    if os.environ.get("FMP_API_KEY"):
        return "fmp"
    return "yfinance"


def _fmp_get(path: str, params: Optional[dict] = None) -> Any:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise RuntimeError("FMP_API_KEY not set")
    q = dict(params or {})
    q["apikey"] = key
    url = f"{FMP_BASE}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-trading-bot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fmp_quote(symbol: str) -> Optional[Dict[str, Any]]:
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    data = _fmp_get("/stable/quote", {"symbol": sym})
    if not isinstance(data, list) or not data:
        return None
    row = data[0] if isinstance(data[0], dict) else None
    if not row:
        return None
    price = row.get("price") or row.get("previousClose")
    if price is None:
        return None
    return {
        "symbol": row.get("symbol") or sym,
        "name": row.get("name") or sym,
        "price": float(price),
        "open": _f(row.get("open")),
        "day_high": _f(row.get("dayHigh")),
        "day_low": _f(row.get("dayLow")),
        "previous_close": _f(row.get("previousClose")),
        "volume": _f(row.get("volume")),
        "avg_volume": _f(row.get("avgVolume") or row.get("averageVolume")),
        "percent_change": _f(row.get("changePercentage") or row.get("changesPercentage")),
        "change": _f(row.get("change")),
        "exchange": row.get("exchange") or "",
        "market_cap": _f(row.get("marketCap")),
        "source": "fmp",
    }


def fmp_quotes(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    cleaned = [s.upper().strip() for s in symbols if s]
    if not cleaned:
        return out
    # FMP stable quote supports comma-separated symbols on many plans
    joined = ",".join(cleaned)
    try:
        data = _fmp_get("/stable/quote", {"symbol": joined})
        if isinstance(data, list):
            for row in data:
                if not isinstance(row, dict):
                    continue
                sym = (row.get("symbol") or "").upper()
                price = row.get("price") or row.get("previousClose")
                if not sym or price is None:
                    continue
                out[sym] = {
                    "symbol": sym,
                    "name": row.get("name") or sym,
                    "price": float(price),
                    "open": _f(row.get("open")),
                    "day_high": _f(row.get("dayHigh")),
                    "day_low": _f(row.get("dayLow")),
                    "previous_close": _f(row.get("previousClose")),
                    "volume": _f(row.get("volume")),
                    "avg_volume": _f(row.get("avgVolume") or row.get("averageVolume")),
                    "percent_change": _f(row.get("changePercentage") or row.get("changesPercentage")),
                    "change": _f(row.get("change")),
                    "exchange": row.get("exchange") or "",
                    "market_cap": _f(row.get("marketCap")),
                    "source": "fmp",
                }
            return out
    except Exception:
        pass
    # fallback one-by-one
    for sym in cleaned:
        try:
            q = fmp_quote(sym)
            if q:
                out[sym] = q
        except Exception:
            continue
    return out


def yf_quote(symbol: str) -> Optional[Dict[str, Any]]:
    if yf is None:
        return None
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
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
            vol = float(hist["Volume"].iloc[-1]) if "Volume" in hist else None
            avg_vol = float(hist["Volume"].tail(10).mean()) if "Volume" in hist else None
        if price is None:
            return None
        pct = None
        if prev:
            pct = (float(price) - prev) / prev * 100.0
        name = sym
        try:
            info = getattr(t, "info", None) or {}
            name = info.get("shortName") or info.get("longName") or sym
        except Exception:
            pass
        return {
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
    except Exception:
        return None


def yf_quotes(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    cleaned = [s.upper().strip() for s in symbols if s]
    if not cleaned or yf is None:
        return out
    try:
        hist = yf.download(
            tickers=" ".join(cleaned),
            period="10d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="ticker",
        )
        for sym in cleaned:
            try:
                if len(cleaned) == 1:
                    df = hist
                else:
                    df = hist[sym] if sym in hist.columns.get_level_values(0) else None
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
                vol = float(df["Volume"].dropna().iloc[-1]) if "Volume" in df and not df["Volume"].dropna().empty else None
                avg_vol = float(df["Volume"].tail(10).mean()) if "Volume" in df else None
                pct = ((price - prev) / prev * 100.0) if prev else None
                out[sym] = {
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
            except Exception:
                q = yf_quote(sym)
                if q:
                    out[sym] = q
    except Exception:
        for sym in cleaned:
            q = yf_quote(sym)
            if q:
                out[sym] = q
    return out


def quote(symbol: str) -> Optional[float]:
    """Best-effort last price float."""
    q = quote_details(symbol)
    if not q:
        return None
    try:
        return float(q["price"])
    except Exception:
        return None


def quote_details(symbol: str) -> Optional[Dict[str, Any]]:
    prov = provider_name()
    if prov == "fmp":
        try:
            q = fmp_quote(symbol)
            if q and q.get("price"):
                return q
        except Exception:
            pass
        # fallback
        return yf_quote(symbol)
    # yfinance primary
    q = yf_quote(symbol)
    if q:
        return q
    if os.environ.get("FMP_API_KEY"):
        try:
            return fmp_quote(symbol)
        except Exception:
            return None
    return None


def quotes_bulk(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    prov = provider_name()
    if prov == "fmp":
        try:
            out = fmp_quotes(symbols)
            if out:
                # fill missing via yfinance
                missing = [s for s in symbols if s.upper() not in out]
                if missing:
                    out.update(yf_quotes(missing))
                return out
        except Exception:
            pass
        return yf_quotes(symbols)
    out = yf_quotes(symbols)
    if len(out) < len([s for s in symbols if s]) and os.environ.get("FMP_API_KEY"):
        try:
            missing = [s for s in symbols if s.upper() not in out]
            out.update(fmp_quotes(missing))
        except Exception:
            pass
    return out


def provider_status() -> Dict[str, Any]:
    return {
        "provider": provider_name(),
        "fmp_key": bool(os.environ.get("FMP_API_KEY")),
        "yfinance": yf is not None,
        "quote_provider_env": os.environ.get("QUOTE_PROVIDER") or "",
    }


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None
