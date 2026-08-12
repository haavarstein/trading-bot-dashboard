"""
Reproduces the SELL-vs-BUY deadlock the review flagged:
juniors vote SELL MSFT (rotation) -> old junior_nomination dropped SELL votes,
so nomination=None -> seniors saw the full market and answered different
questions (one SELL, one BUY) -> no consensus forever.

New behavior: SELL is a first-class nomination, so seniors are pinned to
"SELL MSFT" and cannot answer a different question.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import dual_llm

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(name)


def mk(action, symbol, source="live", conf=70):
    return {"action": action, "symbol": symbol, "confidence": conf, "source": source}


# --- 1. The review's exact deadlock: 3/4 juniors SELL MSFT -------------------
juniors = [
    mk("SELL", "MSFT", conf=80),
    mk("SELL", "MSFT", conf=75),
    mk("SELL", "MSFT", conf=72),
    mk("HOLD", "CASH", conf=60),
]
nom = dual_llm.junior_nomination(juniors, min_agree=3)
check("3x SELL MSFT nominates (SELL, MSFT)", nom, ("SELL", "MSFT"))

# --- 2. A BUY nomination still works (backward compat) ----------------------
juniors = [
    mk("BUY", "CVX", conf=80),
    mk("BUY", "CVX", conf=74),
    mk("BUY", "CVX", conf=71),
    mk("HOLD", "CASH", conf=60),
]
nom = dual_llm.junior_nomination(juniors, min_agree=3)
check("3x BUY CVX nominates (BUY, CVX)", nom, ("BUY", "CVX"))

# --- 3. Split votes (the SELL-vs-BUY mismatch case) produce NO nomination ---
# 2 SELL MSFT + 2 BUY CVX: no pair reaches 3 => no nomination (seniors stay
# full-market; that is acceptable when the board itself is split).
juniors = [
    mk("SELL", "MSFT", conf=75),
    mk("SELL", "MSFT", conf=72),
    mk("BUY", "CVX", conf=78),
    mk("BUY", "CVX", conf=74),
]
nom = dual_llm.junior_nomination(juniors, min_agree=3)
check("split 2/2 => None", nom, None)

# --- 4. Fallback-only juniors never nominate --------------------------------
juniors = [
    mk("SELL", "MSFT", conf=80, source="fallback"),
    mk("SELL", "MSFT", conf=75, source="fallback"),
    mk("SELL", "MSFT", conf=72, source="fallback"),
    mk("HOLD", "CASH", conf=60, source="fallback"),
]
nom = dual_llm.junior_nomination(juniors, min_agree=3)
check("fallback-only => None", nom, None)

# --- 5. min_agree quorum not met --------------------------------------------
juniors = [
    mk("SELL", "MSFT", conf=80),
    mk("SELL", "MSFT", conf=75),
    mk("BUY", "CVX", conf=70),
    mk("HOLD", "CASH", conf=60),
]
nom = dual_llm.junior_nomination(juniors, min_agree=3)
check("2 SELL MSFT (below 3) => None", nom, None)

# --- 6. CASH / empty symbols excluded ---------------------------------------
juniors = [
    mk("SELL", "CASH", conf=80),
    mk("SELL", "", conf=75),
    mk("BUY", "CVX", conf=80),
    mk("BUY", "CVX", conf=76),
    mk("BUY", "CVX", conf=70),
]
nom = dual_llm.junior_nomination(juniors, min_agree=3)
check("CASH/empty ignored, 3x BUY CVX => (BUY, CVX)", nom, ("BUY", "CVX"))

# --- 7. Latent-crash guard: missing nominated symbol in market --------------
# _candidates_brief must not crash on a None payload.
market = {"MSFT": None, "CVX": {"price": 55.0, "rank_score": 80}}
brief = dual_llm._candidates_brief(market, limit=8)
check("_candidates_brief skips None payload", "MSFT" not in brief and "CVX" in brief, True)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("ALL TESTS PASSED")
