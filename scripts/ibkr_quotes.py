#!/usr/bin/env python3
"""
IBKR official-gateway quote reader (read-only).

Uses the official `ib_async` client against the official standalone IBKR Gateway
(TWS API socket). Delayed market data (no subscription required). Emits JSON on
stdout so the sync market_data layer can call it via subprocess.

Usage:
    python scripts/ibkr_quotes.py UNH,XOM,JPM
    python scripts/ibkr_quotes.py UNH,XOM,JPM --timeout 40

Env overrides:
    IBKR_HOST (default 127.0.0.1)
    IBKR_PORT (default 4001)   # 4001 live / 7497 paper gateway socket
    IBKR_CLIENT_ID (default 10)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

try:
    from ib_async import IB, Stock
except Exception as exc:  # pragma: no cover
    print(json.dumps({"ok": False, "error": f"ib_async not installed: {exc}"}))
    sys.exit(0)


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _parse_args(argv) -> tuple[list[str], float]:
    symbols = []
    timeout = 45.0
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--timeout" and i + 1 < len(argv):
            try:
                timeout = float(argv[i + 1])
            except Exception:
                pass
            i += 2
            continue
        if not a.startswith("-"):
            symbols.extend(s.strip().upper() for s in a.split(",") if s.strip())
        i += 1
    return symbols, timeout


async def _main(symbols: list[str], timeout: float) -> None:
    host = os.environ.get("IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("IBKR_PORT", "4001"))
    client_id = int(os.environ.get("IBKR_CLIENT_ID", "10"))
    ib = IB()
    try:
        await ib.connectAsync(host, port, clientId=client_id, readonly=True, timeout=15)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"connect failed ({host}:{port}): {exc}"}))
        return
    ib.reqMarketDataType(3)  # delayed

    results = []
    errors = []
    for sym in symbols:
        c = Stock(sym, "SMART", "USD")
        try:
            await ib.qualifyContractsAsync(c)
        except Exception as exc:
            errors.append({"symbol": sym, "error": f"qualify: {exc}"})
            continue
        try:
            t = ib.reqMktData(c, "", False, False)
            await asyncio.sleep(2.2)
            row = {
                "symbol": sym,
                "price": _num(t.last),
                "bid": _num(t.bid),
                "ask": _num(t.ask),
                "close": _num(t.close),
                "market_data_type": getattr(t, "marketDataType", None),
            }
            results.append(row)
            ib.cancelMktData(c)
        except Exception as exc:
            errors.append({"symbol": sym, "error": f"market data: {exc}"})

    try:
        await ib.disconnectAsync()
    except Exception:
        try:
            ib.disconnect()
        except Exception:
            pass
    print(json.dumps({"ok": True, "accounts": list(ib.managedAccounts()), "quotes": results, "errors": errors}))


if __name__ == "__main__":
    syms, to = _parse_args(sys.argv[1:])
    if not syms:
        print(json.dumps({"ok": False, "error": "no symbols provided"}))
        sys.exit(0)
    asyncio.run(_main(syms, to))
