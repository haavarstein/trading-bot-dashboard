#!/usr/bin/env python3
"""
Ticket 13 — cash-account same-day exit block + unsettled-proceeds lock.

Guards PDT (day-trade) flags and cash-account good-faith reuse:
- Same-day (ET calendar date) discretionary exits (rotation / desk SELL /
  take-profit) are hard-blocked; only stop_loss may exit same-day.
- Sale proceeds are not buying power until the next NYSE session open (09:30 ET).
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.autotrader import DryRunAutoTrader  # noqa: E402
from scripts.paper_broker import PaperBroker, _ET, utc_now  # noqa: E402


def _make_trader(root: Path, **overrides) -> DryRunAutoTrader:
    data_dir = root / "data"
    cfg = {
        "mode": "PAPER_TRADING",
        "enabled": True,
        "account": {"account_id": "SIM", "starting_capital": 1000,
                    "cash_account": True, "margin_enabled": False},
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
        "execution_rules": {
            "allow_rotation": True,
            "rotation_min_score": 70,
            "rotation_min_hold_minutes": 45,
            "block_same_day_exits": True,
            "same_day_exit_allow": ["stop_loss"],
            "unsettled_proceeds_until": "next_session_open",
        },
    }
    cfg.update(overrides)
    path = root / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return DryRunAutoTrader(config_path=str(path))


def _opened(days_ago: int = 0, hour: int = 10) -> str:
    """opened_at timestamp: today (0) or `days_ago` ET calendar days back, 10:00 ET."""
    now_et = datetime.now(_ET)
    d = now_et - timedelta(days=days_ago)
    d = d.replace(hour=hour, minute=0, second=0, microsecond=0)
    return d.astimezone(timezone.utc).isoformat()


class TestSameDayExitBlock(unittest.TestCase):
    """Cash-account hard-block on same-day discretionary exits."""

    def test_same_day_rotation_sell_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            snapshot = {
                "cash": 1000.0, "buying_power": 1000.0,
                "positions": [{
                    "symbol": "MSFT", "qty": 0.4, "avg_cost": 495.0,
                    "last_price": 495.0, "stop_loss": 480.0, "take_profit": 520.0,
                    "opened_at": _opened(days_ago=0),  # opened today ET
                }],
            }
            decision = {"action": "SELL", "symbol": "MSFT", "entry_price": 495.0,
                        "qty": 0.4, "reason_code": "rotation", "confidence": 78}
            ok, reason = trader.validate_order(decision, snapshot)
            self.assertFalse(ok)
            self.assertEqual(reason, "SAME_DAY_EXIT_BLOCKED")

    def test_same_day_take_profit_risk_gate_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            snapshot = {
                "cash": 1000.0, "buying_power": 1000.0,
                "positions": [{
                    "symbol": "MSFT", "qty": 0.4, "avg_cost": 495.0,
                    "current_price": 525.0,  # at/above target
                    "stop_loss": 480.0, "take_profit": 520.0,
                    "opened_at": _opened(days_ago=0),
                }],
            }
            exit_dec = trader.check_deterministic_exit(snapshot)
            # take-profit same-day -> skipped (None, no SELL emitted)
            self.assertIsNone(exit_dec)

    def test_same_day_stop_loss_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            snapshot = {
                "cash": 1000.0, "buying_power": 1000.0,
                "positions": [{
                    "symbol": "MSFT", "qty": 0.4, "avg_cost": 495.0,
                    "current_price": 475.0,  # at/below stop
                    "stop_loss": 480.0, "take_profit": 520.0,
                    "opened_at": _opened(days_ago=0),
                }],
            }
            exit_dec = trader.check_deterministic_exit(snapshot)
            self.assertIsNotNone(exit_dec, "stop-loss must fire same-day")
            self.assertEqual(exit_dec["reason_code"], "stop_loss")
            # and validate_order accepts it
            ok, reason = trader.validate_order(exit_dec, snapshot)
            self.assertTrue(ok, reason)

    def test_next_et_day_sell_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            snapshot = {
                "cash": 1000.0, "buying_power": 1000.0,
                "positions": [{
                    "symbol": "MSFT", "qty": 0.4, "avg_cost": 495.0,
                    "current_price": 525.0,
                    "stop_loss": 480.0, "take_profit": 520.0,
                    "opened_at": _opened(days_ago=1),  # prior ET day
                }],
            }
            # take-profit on a prior-day open is fine
            exit_dec = trader.check_deterministic_exit(snapshot)
            self.assertIsNotNone(exit_dec)
            self.assertEqual(exit_dec["reason_code"], "take_profit")
            # rotation SELL also fine next day
            decision = {"action": "SELL", "symbol": "MSFT", "entry_price": 525.0,
                        "qty": 0.4, "reason_code": "rotation", "confidence": 78}
            ok, reason = trader.validate_order(decision, snapshot)
            self.assertTrue(ok, reason)


class TestUnsettledProceedsLock(unittest.TestCase):
    """Cash-account good-faith: sale proceeds are not buying power until next 09:30 ET."""

    def _broker(self, td: str) -> PaperBroker:
        return PaperBroker(starting_cash=1000.0, path=Path(td) / "portfolio.json",
                           cash_account=True, unsettled_proceeds_until="next_session_open")

    def test_sell_then_buy_same_session_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            b = self._broker(td)
            b.buy("MSFT", 0.4, 500.0, stop_loss=480.0, take_profit=520.0)
            b.sell("MSFT", price=505.0, reason="rotation")
            snap = b.snapshot()
            # proceeds are unsettled -> settled cash is reduced
            self.assertGreater(snap["unsettled_cash"], 0)
            self.assertLess(snap["buying_power"], snap["cash"])
            # a new BUY sized beyond settled cash (900 > ~800 settled, but <= total)
            # must fail because it would reuse unsettled proceeds
            with self.assertRaises(ValueError):
                b.buy("NVDA", 3.0, 300.0, stop_loss=290.0, take_profit=330.0)

    def test_proceeds_release_after_next_session_open(self):
        with tempfile.TemporaryDirectory() as td:
            b = self._broker(td)
            b.buy("MSFT", 0.4, 500.0, stop_loss=480.0, take_profit=520.0)
            b.sell("MSFT", price=505.0, reason="rotation")
            # force release time into the past (simulate next 09:30 ET arrived)
            b.state["unsettled_release_at"] = (
                datetime.now(_ET) - timedelta(minutes=1)
            ).astimezone(timezone.utc).isoformat()
            snap = b.snapshot()
            self.assertEqual(snap["unsettled_cash"], 0.0)
            self.assertEqual(snap["buying_power"], snap["cash"])
            # now the buy can go through
            b.buy("NVDA", 1.0, 300.0, stop_loss=290.0, take_profit=330.0)
            self.assertIn("NVDA", b.state.get("positions", {}))

    def test_non_cash_account_no_lock(self):
        with tempfile.TemporaryDirectory() as td:
            b = PaperBroker(starting_cash=1000.0, path=Path(td) / "portfolio.json",
                            cash_account=False)
            b.buy("MSFT", 0.4, 500.0, stop_loss=480.0, take_profit=520.0)
            b.sell("MSFT", price=505.0, reason="rotation")
            snap = b.snapshot()
            self.assertEqual(snap["unsettled_cash"], 0.0)
            self.assertEqual(snap["buying_power"], snap["cash"])


if __name__ == "__main__":
    unittest.main()
