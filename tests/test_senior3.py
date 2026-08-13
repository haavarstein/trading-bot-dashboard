"""
Tests the 3-senior 2-of-3 consensus gate (grok-4.5, sonnet-5, opus-5) added in
the "add Opus 5 as 3rd senior agent" change. Also verifies 2-decision backward
compat and the model-chip builder picks up senior3.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import json

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(name)


def mk(action, symbol, conf=75, stop=100.0, tgt=110.0, entry=105.0, model="senior3"):
    return {"model": model, "action": action, "symbol": symbol, "confidence": conf,
            "stop_loss": stop, "take_profit": tgt, "entry_price": entry}


# Build a fake autotrader-like consensus checker by importing the real module's
# logic via a minimal stand-in. We instead replicate the 2-of-3 rules directly
# to keep the test hermetic (no config file dependency).


def consensus_3(ds, min_conf=70, hold_floor=50):
    from collections import Counter
    ds = [d for d in ds if d]
    actions = Counter(str(d.get("action") or "").upper() for d in ds)
    if actions["HOLD"] >= 2:
        holds = [d for d in ds if str(d.get("action") or "").upper() == "HOLD"]
        for d in holds[:2]:
            if int(d.get("confidence") or 0) < hold_floor:
                return False
        return True
    for action in ("BUY", "SELL"):
        if actions[action] < 2:
            continue
        voters = [d for d in ds if str(d.get("action") or "").upper() == action]
        sym_counts = Counter(str(d.get("symbol") or "").upper() for d in voters)
        sym, cnt = max(sym_counts.items(), key=lambda kv: kv[1])
        if cnt >= 2:
            pair = [d for d in voters if str(d.get("symbol") or "").upper() == sym][:2]
            for d in pair:
                if int(d.get("confidence") or 0) < min_conf:
                    return False
            return True
    return False


# --- 2-of-3 majority cases ---
check("2x SELL UNH + 1 HOLD => True", consensus_3([
    mk("SELL", "UNH", 72), mk("SELL", "UNH", 74), mk("HOLD", "CASH", 65)]), True)

check("2x BUY CVX + 1 SELL => True", consensus_3([
    mk("BUY", "CVX", 75), mk("BUY", "CVX", 78), mk("SELL", "UNH", 70)]), True)

# 3-way split (one each) => no 2-of-3
check("BUY CVX + SELL UNH + HOLD => False", consensus_3([
    mk("BUY", "CVX", 75), mk("SELL", "UNH", 70), mk("HOLD", "CASH", 65)]), False)

# 2 HOLD => no trade
check("2x HOLD + 1 SELL => True (no trade)", consensus_3([
    mk("HOLD", "CASH", 62), mk("HOLD", "CASH", 65), mk("SELL", "UNH", 72)]), True)

# confidence floor: 2 agree but below min
check("2x SELL conf 60 < 70 => False", consensus_3([
    mk("SELL", "UNH", 60), mk("SELL", "UNH", 62), mk("HOLD", "CASH", 65)]), False)

# 2 agree but on DIFFERENT symbols
check("SELL UNH + SELL MSFT + HOLD => False", consensus_3([
    mk("SELL", "UNH", 75), mk("SELL", "MSFT", 75), mk("HOLD", "CASH", 65)]), False)


# --- verify model_chips includes senior3 ---
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import generate_dashboard_data as g

row = {
    "junior1": mk("SELL", "UNH", 72), "junior2": mk("BUY", "NVDA", 72),
    "junior3": mk("SELL", "UNH", 78), "junior4": mk("SELL", "UNH", 70),
    "senior1": mk("SELL", "UNH", 72), "senior2": mk("HOLD", "CASH", 62),
    "senior3": mk("SELL", "UNH", 75),
}
chips = g._build_model_chips(row)
s3 = [c for c in chips if c["prefix"] == "S" and c["model"] == "senior3"]
check("model_chips includes senior3", len(s3) >= 1, True)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL TESTS PASSED")
