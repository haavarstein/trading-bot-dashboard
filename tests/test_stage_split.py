#!/usr/bin/env python3
"""Tests for the stage-split fix: deterministic exits + junior nomination."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dual_llm  # noqa: E402


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


class TestJuniorNomination(unittest.TestCase):
    def test_nomination_on_majority_symbol(self):
        js = [
            _junior("BUY", "MSFT"),
            _junior("BUY", "MSFT"),
            _junior("BUY", "MSFT"),
            _junior("HOLD", "CASH"),
        ]
        self.assertEqual(dual_llm.junior_nomination(js, min_agree=3), "MSFT")

    def test_no_nomination_when_split(self):
        js = [
            _junior("BUY", "MSFT"),
            _junior("BUY", "AMZN"),
            _junior("HOLD", "CASH"),
            _junior("HOLD", "CASH"),
        ]
        self.assertIsNone(dual_llm.junior_nomination(js, min_agree=3))

    def test_fallback_juniors_do_not_nominate(self):
        js = [
            _junior("BUY", "MSFT", source="fallback"),
            _junior("BUY", "MSFT", source="fallback"),
            _junior("BUY", "MSFT", source="fallback"),
            _junior("BUY", "MSFT", source="fallback"),
        ]
        self.assertIsNone(dual_llm.junior_nomination(js, min_agree=3))

    def test_all_hold_no_nomination(self):
        js = [_junior("HOLD", "CASH") for _ in range(4)]
        self.assertIsNone(dual_llm.junior_nomination(js, min_agree=3))


class TestDeterministicExit(unittest.TestCase):
    def test_stop_hit_returns_exit(self):
        from autotrader import DryRunAutoTrader

        import tempfile
        import json

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            data.mkdir()
            cfg = {
                "mode": "PAPER_TRADING",
                "enabled": True,
                "account": {"account_id": "SIM", "starting_capital": 1000},
                "kill_switch": {"file_path": str(root / "K.txt")},
                "data_files": {
                    "trade_journal": str(data / "a.jsonl"),
                    "order_ledger": str(data / "b.jsonl"),
                    "consensus_log": str(data / "c.jsonl"),
                },
                "consensus_rules": {"min_confidence": 70},
                "position_limits": {"max_position_size_usd": 200, "min_position_size_usd": 50, "max_positions": 5},
                "order_limits": {"min_risk_reward_ratio": 1.5},
                "risk_rules": {"min_stop_distance_pct": 1, "max_stop_distance_pct": 5, "max_loss_per_day_pct": 3},
                "allowed_symbols": "AI_DECIDES",
                "telegram": {"enabled": False},
                "execution_rules": {"allow_rotation": False},
            }
            cp = root / "config.json"
            cp.write_text(json.dumps(cfg), encoding="utf-8")
            trader = DryRunAutoTrader(str(cp))

            snap = {
                "cash": 5.0,
                "buying_power": 5.0,
                "equity": 1000.0,
                "positions": [
                    {
                        "symbol": "PANW",
                        "qty": 0.5,
                        "avg_cost": 400.0,
                        "current_price": 388.0,  # below stop 390
                        "stop_loss": 390.0,
                        "take_profit": 430.0,
                    }
                ],
            }
            d = trader.check_deterministic_exit(snap)
            self.assertIsNotNone(d)
            self.assertEqual(d["action"], "SELL")
            self.assertEqual(d["symbol"], "PANW")
            self.assertEqual(d["reason_code"], "stop_loss")
            self.assertEqual(d["stage"], "exit")

    def test_no_exit_when_within_range(self):
        from autotrader import DryRunAutoTrader

        import tempfile
        import json

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            data.mkdir()
            cfg = {
                "mode": "PAPER_TRADING",
                "enabled": True,
                "account": {"account_id": "SIM", "starting_capital": 1000},
                "kill_switch": {"file_path": str(root / "K.txt")},
                "data_files": {
                    "trade_journal": str(data / "a.jsonl"),
                    "order_ledger": str(data / "b.jsonl"),
                    "consensus_log": str(data / "c.jsonl"),
                },
                "consensus_rules": {"min_confidence": 70},
                "position_limits": {"max_position_size_usd": 200, "min_position_size_usd": 50, "max_positions": 5},
                "order_limits": {"min_risk_reward_ratio": 1.5},
                "risk_rules": {"min_stop_distance_pct": 1, "max_stop_distance_pct": 5, "max_loss_per_day_pct": 3},
                "allowed_symbols": "AI_DECIDES",
                "telegram": {"enabled": False},
                "execution_rules": {"allow_rotation": False},
            }
            cp = root / "config.json"
            cp.write_text(json.dumps(cfg), encoding="utf-8")
            trader = DryRunAutoTrader(str(cp))
            snap = {
                "cash": 5.0,
                "buying_power": 5.0,
                "equity": 1000.0,
                "positions": [
                    {
                        "symbol": "PANW",
                        "qty": 0.5,
                        "avg_cost": 400.0,
                        "current_price": 405.0,  # within range
                        "stop_loss": 390.0,
                        "take_profit": 430.0,
                    }
                ],
            }
            self.assertIsNone(trader.check_deterministic_exit(snap))


if __name__ == "__main__":
    unittest.main()
