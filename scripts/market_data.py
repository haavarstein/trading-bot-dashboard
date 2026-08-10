#!/usr/bin/env python3
"""
Market data provider layer.

FMP free tier: single-symbol /stable/quote only (comma bulk -> 402 Payment Required).
Strategy:
  - Multi-symbol paths prefer yfinance bulk (cheap/free)
  - FMP singles for high-value one-offs, with daily budget + short TTL cache
  - Never log API keys
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = float(os.environ.get("QUOTE_CACHE_TTL_SEC", "90"))
_BUDGET_PATH = Path(__file__).resolve().parent.parent / "data" / "fmp_call_budget.json"


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _budget_state() -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    default = {
        "date": today,
        "calls": 0,
        "limit": int(os.environ.get("FMP_DAILY_CALL_LIMIT", "200")),
    }
    try:
        if _BUDGET_PATH.exists():
            st = json.loads(_BUDGET_PATH.read_text(encoding="utf-8"))
            if st.get("date") != today:
                return default
            st.setdefault("limit", default["limit"])
            return st
    except Exception:
        pass
    return default


def _budget_remaining() -> int:
    st = _budget_state()
    return max(0, int(st.get("limit", 200)) - int(st.get("calls", 0)))


def _budget_inc(n: int = 1) -> None:
    st = _budget_state()
    st["calls"] = int(st.get("calls", 0)) + int(n)
    try:
        _BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BUDGET_PATH.write_text(json.dumps(st, indent=2), encoding="utf-8")
    except Exception:
        pass


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


def provider_name() -> str:
    """
    Preferred single-quote provider.
    multi-symbol bulk always prefers yfinance on free FMP tiers.
    """
    pref = (os.environ.get("QUOTE_PROVIDER") or "fmp").strip().lower()
    if pref == "yfinance":
        return "yfinance"
    if os.environ.get("FMP_API_KEY") and _budget_remaining() > 0:
        return "fmp"
    return "yfinance"


def _fmp_get(path: str, params: Optional[dict] = None) -> Any:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise RuntimeError("FMP_API_KEY not set")
    if _budget_remaining() <= 0:
        try:
            from credit_alerts import notify_credit_issue
            notify_credit_issue("fmp", "FMP daily call budget exhausted (local guard)", http_status=None)
        except Exception:
            pass
        raise RuntimeError("FMP daily call budget exhausted")
    q = dict(params or {})
    q["apikey"] = key
    url = f"{FMP_BASE}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-trading-bot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        _budget_inc(1)
        return data
    except urllib.error.HTTPError as e:
        try:
            _budget_inc(1)
        except Exception:
            pass
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            body = str(e)
        detail = f"FMP HTTP {getattr(e, 'code', '?')}: {body or e}"
        if getattr(e, "code", None) in (402, 429) or "limit" in detail.lower() or "credit" in detail.lower():
            try:
                from credit_alerts import notify_credit_issue
                notify_credit_issue("fmp", detail, http_status=getattr(e, "code", None))
            except Exception:
                pass
        raise
    except Exception as e:
        if "budget exhausted" in str(e).lower():
            try:
                from credit_alerts import notify_credit_issue
                notify_credit_issue("fmp", str(e), http_status=None)
            except Exception:
                pass
        raise


def _row_from_fmp(row: dict, fallback_sym: str = "") -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    sym = (row.get("symbol") or fallback_sym or "").upper()
    price = row.get("price") or row.get("previousClose")
    if not sym or price is None:
        return None
    out = {
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
    _cache_set(f"fmp:{sym}", out)
    return out


def fmp_quote(symbol: str) -> Optional[Dict[str, Any]]:
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    cached = _cache_get(f"fmp:{sym}")
    if cached:
        return cached
    data = _fmp_get("/stable/quote", {"symbol": sym})
    if not isinstance(data, list) or not data:
        return None
    return _row_from_fmp(data[0], sym)


def fmp_quotes(symbols: List[str], allow_singles: bool = False, max_singles: int = 8) -> Dict[str, Dict[str, Any]]:
    """
    Free tier: comma bulk is paid (402). Do not spam singles unless allow_singles.
    """
    out: Dict[str, Dict[str, Any]] = {}
    cleaned = []
    for s in symbols:
        sym = (s or "").upper().strip()
        if not sym:
            continue
        cached = _cache_get(f"fmp:{sym}")
        if cached:
            out[sym] = cached
        else:
            cleaned.append(sym)
    if not cleaned:
        return out

    # Try bulk once (works on paid plans). On 402/fail, skip unless allow_singles.
    joined = ",".join(cleaned)
    try:
        data = _fmp_get("/stable/quote", {"symbol": joined})
        if isinstance(data, list) and data:
            for row in data:
                parsed = _row_from_fmp(row)
                if parsed:
                    out[parsed["symbol"]] = parsed
            # if bulk returned everything, done
            if all(s in out for s in cleaned):
                return out
    except Exception:
        pass

    if not allow_singles:
        return out

    remaining = [s for s in cleaned if s not in out]
    budget = min(len(remaining), max_singles, _budget_remaining())
    for sym in remaining[:budget]:
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
    # serve cache first
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


def quotes_bulk(
    symbols: List[str],
    prefer: str | None = None,
    allow_fmp_singles: bool = False,
    max_fmp_singles: int = 8,
) -> Dict[str, Dict[str, Any]]:
    """
    Efficient multi-symbol quotes.
    Default: yfinance bulk first (free tier friendly), optional FMP singles fill.
    """
    cleaned = [s.upper().strip() for s in symbols if s]
    if not cleaned:
        return {}
    mode = (prefer or os.environ.get("BULK_QUOTE_PROVIDER") or "yfinance").strip().lower()

    out: Dict[str, Dict[str, Any]] = {}
    if mode == "fmp":
        out = fmp_quotes(cleaned, allow_singles=allow_fmp_singles, max_singles=max_fmp_singles)
        missing = [s for s in cleaned if s not in out]
        if missing:
            out.update(yf_quotes(missing))
        return out

    # yfinance primary bulk
    out = yf_quotes(cleaned)
    missing = [s for s in cleaned if s not in out]
    if missing and allow_fmp_singles and os.environ.get("FMP_API_KEY"):
        out.update(fmp_quotes(missing, allow_singles=True, max_singles=max_fmp_singles))
    return out


def quote_details(symbol: str) -> Optional[Dict[str, Any]]:
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    # single symbol: FMP if preferred and budget remains
    if provider_name() == "fmp":
        try:
            q = fmp_quote(sym)
            if q and q.get("price"):
                return q
        except Exception:
            pass
        return yf_quote(sym)
    q = yf_quote(sym)
    if q:
        return q
    if os.environ.get("FMP_API_KEY") and _budget_remaining() > 0:
        try:
            return fmp_quote(sym)
        except Exception:
            return None
    return None


def quote(symbol: str) -> Optional[float]:
    q = quote_details(symbol)
    if not q:
        return None
    try:
        return float(q["price"])
    except Exception:
        return None


def provider_status() -> Dict[str, Any]:
    st = _budget_state()
    return {
        "provider": provider_name(),
        "bulk_provider": (os.environ.get("BULK_QUOTE_PROVIDER") or "yfinance"),
        "fmp_key": bool(os.environ.get("FMP_API_KEY")),
        "yfinance": yf is not None,
        "quote_provider_env": os.environ.get("QUOTE_PROVIDER") or "",
        "fmp_calls_today": st.get("calls", 0),
        "fmp_daily_limit": st.get("limit", 200),
        "fmp_remaining": _budget_remaining(),
        "cache_ttl_sec": _CACHE_TTL,
    }
