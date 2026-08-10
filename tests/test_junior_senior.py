#!/usr/bin/env python3
"""Unit tests for junior→senior escalation policy (no live API calls)."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dual_llm  # noqa: E402


class TestJuniorSeniorEscalation(unittest.TestCase):
    def test_hold_hold_stays_junior(self):
        j1 = {"action": "HOLD", "symbol": "CASH", "confidence": 62}
        j2 = {"action": "HOLD", "symbol": "MSFT", "confidence": 68}
        ok, _ = dual_llm.junior_pair_agrees(j1, j2, hold_floor=55)
        self.assertTrue(ok)
        esc, reason = dual_llm.should_escalate_to_seniors(
            j1,
            j2,
            ok,
            {
                "min_confidence": 70,
                "escalate_on_buy_sell": True,
                "escalate_on_junior_disagree": True,
                "escalate_on_borderline_confidence": True,
                "borderline_confidence_band": 5,
            },
        )
        self.assertFalse(esc, reason)
        self.assertEqual(reason, "junior_hold_final")

    def test_buy_escalates_even_if_juniors_agree(self):
        j1 = {"action": "BUY", "symbol": "MSFT", "confidence": 80}
        j2 = {"action": "BUY", "symbol": "MSFT", "confidence": 82}
        ok, _ = dual_llm.junior_pair_agrees(j1, j2)
        self.assertTrue(ok)
        esc, reason = dual_llm.should_escalate_to_seniors(
            j1,
            j2,
            ok,
            {"min_confidence": 70, "escalate_on_buy_sell": True},
        )
        self.assertTrue(esc)
        self.assertEqual(reason, "buy_sell_requires_senior_gate")

    def test_junior_disagree_escalates(self):
        j1 = {"action": "HOLD", "symbol": "CASH", "confidence": 70}
        j2 = {"action": "BUY", "symbol": "MSFT", "confidence": 75}
        ok, _ = dual_llm.junior_pair_agrees(j1, j2)
        self.assertFalse(ok)
        esc, reason = dual_llm.should_escalate_to_seniors(
            j1,
            j2,
            ok,
            {"min_confidence": 70, "escalate_on_junior_disagree": True},
        )
        self.assertTrue(esc)
        self.assertEqual(reason, "junior_disagreement")

    def test_run_junior_only_path_with_fallback_fn(self):
        def fb(name, broker, market):
            # deterministic juniors HOLD
            if "haiku" in name or "claude-haiku" in name:
                return {
                    "model": name,
                    "action": "HOLD",
                    "symbol": "CASH",
                    "confidence": 66,
                    "entry_price": 0,
                    "stop_loss": 0,
                    "take_profit": 0,
                    "qty": 0,
                    "thesis": "fb hold",
                    "reason_code": "hold",
                }
            return {
                "model": name,
                "action": "HOLD",
                "symbol": "CASH",
                "confidence": 64,
                "entry_price": 0,
                "stop_loss": 0,
                "take_profit": 0,
                "qty": 0,
                "thesis": "fb hold",
                "reason_code": "hold",
            }

        # Force fallback by unsetting keys temporarily
        old_x = os.environ.pop("XAI_API_KEY", None)
        old_g = os.environ.pop("GROK_API_KEY", None)
        old_a = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            def senior_check(d1, d2):
                return True, None

            out = dual_llm.run_junior_senior_consensus(
                {"cash": 1000, "equity": 1000, "positions": []},
                {"MSFT": {"price": 500, "rank_score": 70, "catalyst_score": 5, "sentiment": "mixed"}},
                {
                    "junior_enabled": True,
                    "junior_model_1": "grok-4.3",
                    "junior_model_2": "claude-haiku-4-5",
                    "model_1": "grok-4.5",
                    "model_2": "claude-sonnet-5",
                    "min_confidence": 70,
                    "escalate_on_buy_sell": True,
                },
                fb,
                senior_check,
            )
            self.assertEqual(out["tier"], "junior_only")
            self.assertTrue(out["consensus"])
            self.assertEqual(out["decision1"]["action"], "HOLD")
        finally:
            if old_x is not None:
                os.environ["XAI_API_KEY"] = old_x
            if old_g is not None:
                os.environ["GROK_API_KEY"] = old_g
            if old_a is not None:
                os.environ["ANTHROPIC_API_KEY"] = old_a


if __name__ == "__main__":
    unittest.main()
