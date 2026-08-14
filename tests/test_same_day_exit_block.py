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


class TestTicket14CashLedgerDebit(unittest.TestCase):
    """buy() must debit the TOTAL cash ledger, not settled cash (no evaporation)."""

    def test_buy_debits_total_cash_keeps_unsettled(self):
        with tempfile.TemporaryDirectory() as td:
            b = PaperBroker(starting_cash=1000.0, path=Path(td) / "portfolio.json",
                            cash_account=True, unsettled_proceeds_until="next_session_open")
            b.buy("MSFT", 0.4, 500.0, stop_loss=480.0, take_profit=520.0)  # cost 200
            b.sell("MSFT", price=505.0, reason="rotation")  # proceeds 202
            snap = b.snapshot()
            self.assertEqual(snap["cash"], 1002.0)
            self.assertEqual(snap["unsettled_cash"], 202.0)
            self.assertEqual(snap["settled_cash"], 800.0)
            old_cash = b.state["cash"]
            old_unsettled = b.state["unsettled_cash"]
            # a BUY that fits leftover SETTLED cash (e.g. $100) must debit TOTAL cash
            b.buy("AAPL", 0.25, 400.0, stop_loss=385.0, take_profit=430.0)  # cost 100
            self.assertEqual(b.state["cash"], round(old_cash - 100.0, 2))
            # unsettled cash unchanged (buy consumed settled, not unsettled)
            self.assertEqual(b.state["unsettled_cash"], old_unsettled)


class TestTicket15BuyingPower(unittest.TestCase):
    """get_broker_snapshot must not clobber buying_power with total cash."""

    def test_get_broker_snapshot_keeps_settled_buying_power(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            # build a SELL then check snapshot buying_power stays settled
            trader.broker.buy("MSFT", 0.4, 500.0, stop_loss=480.0, take_profit=520.0)
            trader.broker.sell("MSFT", price=505.0, reason="rotation")
            snap = trader.get_broker_snapshot()
            self.assertGreater(snap["unsettled_cash"], 0)
            self.assertEqual(snap["buying_power"], snap["settled_cash"])
            self.assertLess(snap["buying_power"], snap["cash"])


class TestTicket16DeskStopLossBypass(unittest.TestCase):
    """A desk SELL tagged stop_loss must not bypass the same-day block when mark>stop."""

    def _decision(self, reason_code, symbol="MSFT", px=495.0):
        return {"action": "SELL", "symbol": symbol, "entry_price": px,
                "qty": 0.4, "reason_code": reason_code, "confidence": 78}

    def _same_day_open(self, px=495.0):
        return {
            "cash": 1000.0, "buying_power": 1000.0,
            "positions": [{
                "symbol": "MSFT", "qty": 0.4, "avg_cost": 495.0,
                "current_price": px, "stop_loss": 480.0, "take_profit": 520.0,
                "opened_at": _opened(days_ago=0),  # today ET
            }],
        }

    def test_desk_stop_loss_above_stop_blocked_same_day(self):
        # mark 495 > stored stop 480 -> NOT a real stop hit -> same-day block
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            ok, reason = trader.validate_order(
                self._decision("stop_loss"), self._same_day_open(px=495.0))
            self.assertFalse(ok)
            self.assertEqual(reason, "SAME_DAY_EXIT_BLOCKED")

    def test_desk_stop_loss_at_below_stop_allowed_same_day(self):
        # mark 475 <= stop 480 -> genuine stop hit -> allowed
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            ok, reason = trader.validate_order(
                self._decision("stop_loss", px=475.0), self._same_day_open(px=475.0))
            self.assertTrue(ok, reason)

    def test_missing_opened_at_fails_closed_for_discretionary_sell(self):
        # no opened_at -> treat as today for a discretionary SELL -> blocked
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            snap = {
                "cash": 1000.0, "buying_power": 1000.0,
                "positions": [{
                    "symbol": "MSFT", "qty": 0.4, "avg_cost": 495.0,
                    "current_price": 495.0, "stop_loss": 480.0, "take_profit": 520.0,
                    # no opened_at key
                }],
            }
            ok, reason = trader.validate_order(self._decision("rotation"), snap)
            self.assertFalse(ok)
            self.assertEqual(reason, "SAME_DAY_EXIT_BLOCKED")

    def test_block_same_day_exits_false_allows(self):
        # config off-switch -> same-day rotation SELL is allowed
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td),
                                  execution_rules={"block_same_day_exits": False})
            snap = self._same_day_open(px=495.0)
            ok, reason = trader.validate_order(self._decision("rotation"), snap)
            self.assertTrue(ok, reason)


class TestNextSessionOpenRelease(unittest.TestCase):
    """Friday 16:00 ET sell -> proceeds release Monday 09:30 ET (weekend skipped)."""

    def test_friday_sell_releases_monday_0930(self):
        # choose a known Friday 2026-08-14 16:00 ET
        friday = datetime(2026, 8, 14, 16, 0, tzinfo=_ET)
        with tempfile.TemporaryDirectory() as td:
            b = PaperBroker(starting_cash=1000.0, path=Path(td) / "portfolio.json",
                            cash_account=True, unsettled_proceeds_until="next_session_open")
            release = b._next_session_open_et(friday)
            self.assertEqual(release.date().isoformat(), "2026-08-17")  # Monday
            self.assertEqual((release.hour, release.minute), (9, 30))
            self.assertIn(release.weekday(), (0, 1, 2, 3, 4))  # weekday


class TestTicket17ZeroBuyingPower(unittest.TestCase):
    """buying_power == 0 (sold whole book) must not fall back to total cash."""

    def test_buy_fitting_total_cash_but_not_settled_blocked(self):
        # Sell the whole book: all proceeds unsettled -> buying_power == 0, cash > 0.
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            snapshot = {
                "buying_power": 0.0,   # settled == 0
                "settled_cash": 0.0,
                "cash": 1002.0,         # total (all unsettled)
                "positions": [],        # whole book sold
            }
            decision = {"action": "BUY", "symbol": "NVDA", "entry_price": 100.0,
                        "qty": 1.0, "stop_loss": 95.0, "take_profit": 120.0,
                        "confidence": 75}  # cost $100, fits total cash, NOT settled
            ok, reason = trader.validate_order(decision, snapshot)
            self.assertFalse(ok)
            self.assertIn("Insufficient buying power", reason)

    def test_buying_power_helper_never_coerces_zero(self):
        with tempfile.TemporaryDirectory() as td:
            trader = _make_trader(Path(td))
            # buying_power present (0.0) -> must stay 0.0, not fall to cash
            self.assertEqual(trader._buying_power({"buying_power": 0.0, "cash": 900.0}), 0.0)
            # buying_power None, settled_cash present -> settled
            self.assertEqual(trader._buying_power({"settled_cash": 42.0, "cash": 900.0}), 42.0)
            # neither -> cash
            self.assertEqual(trader._buying_power({"cash": 900.0}), 900.0)
            # none -> 0
            self.assertEqual(trader._buying_power({}), 0.0)


if __name__ == "__main__":
    unittest.main()
