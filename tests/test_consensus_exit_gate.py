#!/usr/bin/env python3
"""
Regression tests for the no_consensus deadlock.

Each test here reproduces a cycle that the live desk actually logged on
2026-08-13 and that the previous code rejected.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dual_llm  # noqa: E402

from scripts.autotrader import DryRunAutoTrader  # noqa: E402


def _make_trader(root: Path, **overrides) -> DryRunAutoTrader:
    data_dir = root / "data"
    cfg = {
        "mode": "PAPER_TRADING",
        "enabled": True,
        "account": {"account_id": "SIM", "starting_capital": 1000},
        "kill_switch": {"file_path": str(root / "KILL_SWITCH.txt")},
        "data_files": {
            "trade_journal": str(data_dir / "trade_journal.jsonl"),
            "order_ledger": str(data_dir / "order_ledger.jsonl"),
            "consensus_log": str(data_dir / "consensus_log.jsonl"),
        },
        "consensus_rules": {
            "min_confidence": 70,
            "model_1": "grok-4.5",
            "model_2": "claude-sonnet-5",
        },
        "position_limits": {
            "max_position_size_usd": 200,
            "min_position_size_usd": 50,
            "max_positions": 5,
        },
        "order_limits": {"min_risk_reward_ratio": 1.5},
        "risk_rules": {
            "min_stop_distance_pct": 1,
            "max_stop_distance_pct": 5,
            "max_loss_per_day_pct": 5,
        },
        "allowed_symbols": "AI_DECIDES",
        "telegram": {"enabled": False, "chat_id": ""},
    }
    cfg.update(overrides)
    path = root / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return DryRunAutoTrader(config_path=str(path))


def _senior(action, symbol, conf, entry, stop, target):
    return {
        "action": action,
        "symbol": symbol,
        "confidence": conf,
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": target,
    }


class TestExitConsensusIgnoresProtectiveLevels(unittest.TestCase):
    """Stop/target agreement is an entry check; it must not gate exits."""

    def test_sell_reaches_consensus_despite_divergent_stops(self):
        # Live cycle 2026-08-13T18:35Z: both seniors confirmed SELL UNH and the
        # desk still logged "Stop loss mismatch: $410.5 vs $0.0".
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            s1 = _senior("SELL", "UNH", 78, 400.75, 410.5, 385.5)
            s2 = _senior("SELL", "UNH", 72, 400.14, 0.0, 0.0)
            ok, reason = trader.check_consensus(s1, s2)
            self.assertTrue(ok, f"exit was blocked: {reason}")
            self.assertIsNone(reason)

    def test_buy_still_blocked_on_divergent_stops(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            s1 = _senior("BUY", "AMD", 75, 200.00, 195.00, 210.00)
            s2 = _senior("BUY", "AMD", 74, 200.00, 170.00, 210.00)
            ok, reason = trader.check_consensus(s1, s2)
            self.assertFalse(ok)
            self.assertIn("Stop loss mismatch", reason)

    def test_buy_still_blocked_on_divergent_targets(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            s1 = _senior("BUY", "AMD", 75, 200.00, 195.00, 210.00)
            s2 = _senior("BUY", "AMD", 74, 200.00, 195.00, 260.00)
            ok, reason = trader.check_consensus(s1, s2)
            self.assertFalse(ok)
            self.assertIn("Take profit mismatch", reason)

    def test_sell_still_blocked_on_symbol_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            s1 = _senior("SELL", "UNH", 78, 400.75, 410.5, 385.5)
            s2 = _senior("SELL", "XOM", 72, 158.44, 0.0, 0.0)
            ok, reason = trader.check_consensus(s1, s2)
            self.assertFalse(ok)
            self.assertIn("Symbol mismatch", reason)

    def test_sell_still_blocked_below_min_confidence(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            s1 = _senior("SELL", "UNH", 62, 400.75, 410.5, 385.5)
            s2 = _senior("SELL", "UNH", 72, 400.14, 0.0, 0.0)
            ok, reason = trader.check_consensus(s1, s2)
            self.assertFalse(ok)
            self.assertIn("confidence", reason)


class TestRiskRewardTolerance(unittest.TestCase):
    """An exactly-at-threshold plan must not be rejected by float error."""

    def test_rr_at_threshold_validates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            trader = _make_trader(root)
            entry, stop, target = 100.28, 97.82, 103.97
            self.assertLess(abs(target - entry) / abs(entry - stop), 1.5)
            decision = {
                "action": "BUY",
                "symbol": "MSFT",
                "entry_price": entry,
                "stop_loss": stop,
                "take_profit": target,
                "qty": 1.0,
                "confidence": 75,
            }
            snapshot = {"cash": 500.0, "buying_power": 500.0, "positions": []}
            valid, reason = trader.validate_order(decision, snapshot)
            self.assertTrue(valid, f"at-threshold RR blocked: {reason}")

    def test_rr_genuinely_below_threshold_still_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            decision = {
                "action": "BUY",
                "symbol": "MSFT",
                "entry_price": 500.0,
                "stop_loss": 490.0,
                "take_profit": 512.0,  # rr = 1.2
                "qty": 0.2,
                "confidence": 75,
            }
            snapshot = {"cash": 500.0, "buying_power": 500.0, "positions": []}
            valid, reason = trader.validate_order(decision, snapshot)
            self.assertFalse(valid)
            self.assertIn("Risk/Reward", reason)
            # The message must not round a real rejection up to the threshold.
            self.assertNotIn("1.50 < minimum 1.5", reason)


def _junior(action, symbol, conf=72, source="live"):
    return {
        "action": action,
        "symbol": symbol,
        "confidence": conf,
        "source": source,
        "entry_price": 0,
        "stop_loss": 0,
        "take_profit": 0,
        "qty": 0,
        "thesis": "x",
        "reason_code": "hold",
    }


FULL_BOOK = {
    "cash": 30.55,
    "positions": [
        {"symbol": s} for s in ("UNH", "XOM", "JPM", "CVX", "MSFT")
    ],
}
RULES = {"max_positions": 5}


class TestSplitTiebreak(unittest.TestCase):
    """2/2 junior splits on a full book must not deadlock forever."""

    def test_nomination_is_a_tuple_not_a_symbol(self):
        nom = dual_llm.junior_nomination(
            [
                _junior("SELL", "UNH"),
                _junior("SELL", "UNH"),
                _junior("SELL", "UNH"),
                _junior("BUY", "AMD"),
            ],
            min_agree=3,
        )
        self.assertEqual(nom, ("SELL", "UNH"))
        self.assertEqual((nom[1] if nom else "") or "", "UNH")

    def test_even_split_on_full_book_nominates_the_exit(self):
        # Live cycle 2026-08-13T19:04Z: 2x SELL UNH vs 2x BUY AMD -> no
        # nomination -> "Action mismatch: SELL vs BUY".
        js = [
            _junior("SELL", "UNH"),
            _junior("BUY", "AMD"),
            _junior("SELL", "UNH", conf=78),
            _junior("BUY", "AMD"),
        ]
        self.assertIsNone(dual_llm.junior_nomination(js, min_agree=3))
        held = {str(p["symbol"]).upper() for p in FULL_BOOK["positions"]}
        self.assertEqual(
            dual_llm.junior_nomination(
                js, min_agree=3, prefer_exit_when_full=True, held_symbols=held
            ),
            ("SELL", "UNH"),
        )

    def test_no_tiebreak_when_book_has_room(self):
        js = [
            _junior("SELL", "UNH"),
            _junior("BUY", "AMD"),
            _junior("SELL", "UNH"),
            _junior("BUY", "AMD"),
        ]
        room = {"cash": 400.0, "positions": [{"symbol": "UNH"}, {"symbol": "XOM"}]}
        held = {str(p["symbol"]).upper() for p in room["positions"]}
        # Book not full -> tie-break off regardless
        # (simulate by prefer_exit_when_full=False since book_full gate is at call site)
        self.assertIsNone(
            dual_llm.junior_nomination(
                js, min_agree=3, prefer_exit_when_full=False, held_symbols=held
            )
        )

    def test_no_tiebreak_for_unheld_symbol(self):
        js = [
            _junior("SELL", "NVDA"),
            _junior("BUY", "AMD"),
            _junior("SELL", "NVDA"),
            _junior("BUY", "AMD"),
        ]
        held = {str(p["symbol"]).upper() for p in FULL_BOOK["positions"]}
        self.assertIsNone(
            dual_llm.junior_nomination(
                js, min_agree=3, prefer_exit_when_full=True, held_symbols=held
            )
        )

    def test_fallback_juniors_do_not_break_ties(self):
        js = [
            _junior("SELL", "UNH", source="fallback"),
            _junior("SELL", "UNH", source="fallback"),
            _junior("BUY", "AMD"),
            _junior("BUY", "AMD"),
        ]
        held = {str(p["symbol"]).upper() for p in FULL_BOOK["positions"]}
        self.assertIsNone(
            dual_llm.junior_nomination(
                js, min_agree=3, prefer_exit_when_full=True, held_symbols=held
            )
        )

    def test_all_hold_does_not_break_ties(self):
        js = [_junior("HOLD", "CASH") for _ in range(4)]
        held = {str(p["symbol"]).upper() for p in FULL_BOOK["positions"]}
        self.assertIsNone(
            dual_llm.junior_nomination(
                js, min_agree=3, prefer_exit_when_full=True, held_symbols=held
            )
        )


if __name__ == "__main__":
    unittest.main()
