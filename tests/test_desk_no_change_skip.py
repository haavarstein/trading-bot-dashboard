#!/usr/bin/env python3
"""Unit tests for the conservative no-change desk gate (API-cost control)."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import autotrader  # noqa: E402


def _gate(**over):
    cfg = {"enabled": True, "price_move_pct_threshold": 0.5, "stop_target_buffer_pct": 1.0}
    cfg.update(over)
    return cfg


def _state(cands_sig, positions):
    return {"candidates_sig": cands_sig, "positions": positions, "updated_at": "t"}


class TestCandidateSignature(unittest.TestCase):
    def test_changes_when_evidence_changes(self):
        a = [{"symbol": "MSFT", "rank_score": 70, "catalyst_score": 5, "sentiment": "bullish", "rsi": 55, "price": 500, "catalyst": "x"}]
        b = [{"symbol": "MSFT", "rank_score": 71, "catalyst_score": 5, "sentiment": "bullish", "rsi": 55, "price": 500, "catalyst": "x"}]
        self.assertNotEqual(autotrader.candidate_signature(a), autotrader.candidate_signature(b))

    def test_ignores_record_order(self):
        a = [{"symbol": "A", "rank_score": 1}, {"symbol": "B", "rank_score": 2}]
        b = [{"symbol": "B", "rank_score": 2}, {"symbol": "A", "rank_score": 1}]
        self.assertEqual(autotrader.candidate_signature(a), autotrader.candidate_signature(b))


class TestEvaluateDeskSkip(unittest.TestCase):
    SIG = autotrader.candidate_signature([{"symbol": "MSFT", "rank_score": 70}])

    def test_gate_disabled_never_skips(self):
        prev = _state(self.SIG, {})
        self.assertEqual(autotrader.evaluate_desk_skip(prev, self.SIG, {}, _gate(enabled=False)), (False, "gate_disabled"))

    def test_no_prior_state_never_skips(self):
        self.assertEqual(autotrader.evaluate_desk_skip({}, self.SIG, {}, _gate()), (False, "no_prior_state"))

    def test_candidates_changed_does_not_skip(self):
        prev = _state(self.SIG, {})
        new_sig = autotrader.candidate_signature([{"symbol": "AAPL", "rank_score": 80}])
        self.assertEqual(autotrader.evaluate_desk_skip(prev, new_sig, {}, _gate()), (False, "candidates_changed"))

    def test_position_set_changed_does_not_skip(self):
        prev = _state(self.SIG, {})
        cur = {"MSFT": {"price": 500.0}}
        self.assertEqual(autotrader.evaluate_desk_skip(prev, self.SIG, cur, _gate()), (False, "positions_changed"))

    def test_position_moved_beyond_threshold_does_not_skip(self):
        prev = _state(self.SIG, {"MSFT": {"price": 100.0, "stop_loss": 90.0, "take_profit": 120.0}})
        cur = {"MSFT": {"price": 101.0, "stop_loss": 90.0, "take_profit": 120.0}}  # 1.0% move > 0.5% threshold
        skip, reason = autotrader.evaluate_desk_skip(prev, self.SIG, cur, _gate())
        self.assertFalse(skip)
        self.assertIn("moved_MSFT", reason)

    def test_near_stop_does_not_skip(self):
        prev = _state(self.SIG, {"MSFT": {"price": 100.0, "stop_loss": 90.0, "take_profit": 120.0}})
        cur = {"MSFT": {"price": 100.0, "stop_loss": 99.2, "take_profit": 120.0}}  # 0.8% from stop < 1% buffer
        skip, reason = autotrader.evaluate_desk_skip(prev, self.SIG, cur, _gate())
        self.assertFalse(skip)
        self.assertIn("near_stop_loss_MSFT", reason)

    def test_near_target_does_not_skip(self):
        prev = _state(self.SIG, {"MSFT": {"price": 100.0, "stop_loss": 90.0, "take_profit": 120.0}})
        cur = {"MSFT": {"price": 100.0, "stop_loss": 90.0, "take_profit": 100.9}}  # 0.9% from target < 1% buffer
        skip, reason = autotrader.evaluate_desk_skip(prev, self.SIG, cur, _gate())
        self.assertFalse(skip)
        self.assertIn("near_take_profit_MSFT", reason)

    def test_unchanged_within_threshold_skips(self):
        prev = _state(self.SIG, {"MSFT": {"price": 100.0, "stop_loss": 90.0, "take_profit": 120.0}})
        cur = {"MSFT": {"price": 100.3, "stop_loss": 90.0, "take_profit": 120.0}}  # 0.3% move <= 0.5%
        self.assertEqual(autotrader.evaluate_desk_skip(prev, self.SIG, cur, _gate()), (True, "no_change"))

    def test_no_positions_unchanged_skips(self):
        prev = _state(self.SIG, {})
        self.assertEqual(autotrader.evaluate_desk_skip(prev, self.SIG, {}, _gate()), (True, "no_change"))


class TestPositionMapAndState(unittest.TestCase):
    def test_position_prices_map_normalizes(self):
        snap = {"positions": [
            {"symbol": "MSFT", "current_price": 500.0, "stop_loss": 490.0, "take_profit": 530.0},
            {"symbol": "AAPL", "last": 250.0},
        ]}
        m = autotrader.position_prices_map(snap)
        self.assertEqual(m["MSFT"]["price"], 500.0)
        self.assertEqual(m["MSFT"]["stop_loss"], 490.0)
        self.assertEqual(m["AAPL"]["price"], 250.0)
        self.assertIn("updated_at", autotrader.build_desk_state("sig", m))


if __name__ == "__main__":
    unittest.main()
