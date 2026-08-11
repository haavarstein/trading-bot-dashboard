#!/usr/bin/env python3
"""Unit tests for junior→senior escalation + N-junior majority policy (no live API calls)."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import re
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


class TestJuniorMajority(unittest.TestCase):
    def _junior(self, action, conf, source="live"):
        return {
            "action": action,
            "symbol": "CASH" if action == "HOLD" else "MSFT",
            "confidence": conf,
            "source": source,
            "entry_price": 0,
            "stop_loss": 0,
            "take_profit": 0,
            "qty": 0,
            "thesis": "x",
            "reason_code": "hold",
        }

    def test_majority_hold_3of4_finalizes(self):
        js = [
            self._junior("HOLD", 60),
            self._junior("HOLD", 65),
            self._junior("HOLD", 58),
            self._junior("HOLD", 62),
        ]
        ok, reason, tally = dual_llm.junior_majority_vote(js, hold_floor=55, min_agree=3)
        self.assertTrue(ok, reason)
        self.assertEqual(tally["HOLD"], 4)

    def test_live_trade_intent_escalates(self):
        js = [
            self._junior("HOLD", 60),
            self._junior("HOLD", 65),
            self._junior("BUY", 80),  # live trade intent
            self._junior("HOLD", 62),
        ]
        ok, reason, _ = dual_llm.junior_majority_vote(js, hold_floor=55, min_agree=3)
        self.assertFalse(ok, reason)
        self.assertIn("junior_trade_intent", reason)

    def test_quorum_met_3of3_finalizes(self):
        js = [
            self._junior("HOLD", 60),
            self._junior("HOLD", 65),
            self._junior("HOLD", 58),
        ]
        ok, reason, _ = dual_llm.junior_majority_vote(js, hold_floor=55, min_agree=3)
        self.assertTrue(ok, reason)

    def test_quorum_miss_escalates(self):
        # 2 HOLDs but min_agree=3 -> no quorum
        js = [
            self._junior("HOLD", 60),
            self._junior("HOLD", 65),
            self._junior("HOLD", 58),
        ]
        ok, reason, _ = dual_llm.junior_majority_vote(js, hold_floor=55, min_agree=4)
        self.assertFalse(ok, reason)
        self.assertIn("junior_hold_quorum", reason)

    def test_run_junior_only_path_with_fallback_fn(self):
        def fb(name, broker, market):
            return {
                "model": name,
                "action": "HOLD",
                "symbol": "CASH",
                "confidence": 62,
                "entry_price": 0,
                "stop_loss": 0,
                "take_profit": 0,
                "qty": 0,
                "thesis": "fb hold",
                "reason_code": "hold",
            }

        old_x = os.environ.pop("XAI_API_KEY", None)
        old_g = os.environ.pop("GROK_API_KEY", None)
        old_a = os.environ.pop("ANTHROPIC_API_KEY", None)
        old_n = os.environ.pop("NOUS_API_KEY", None)
        try:
            out = dual_llm.run_junior_senior_consensus(
                {"cash": 1000, "equity": 1000, "positions": []},
                {"MSFT": {"price": 500, "rank_score": 70, "catalyst_score": 5, "sentiment": "mixed"}},
                {
                    "junior_enabled": True,
                    "junior_models": ["grok-4.3", "claude-haiku-4-5", "deepseek-v4-flash", "grok-build-0.1"],
                    "junior_min_agree": 3,
                    "model_1": "grok-4.5",
                    "model_2": "claude-sonnet-5",
                    "min_confidence": 70,
                },
                fb,
                lambda d1, d2: (True, None),
            )
            self.assertEqual(out["tier"], "junior_only")
            self.assertTrue(out["consensus"])
            self.assertEqual(out["decision1"]["action"], "HOLD")
            self.assertEqual(len([k for k in out if re.match(r"junior[1-9]", k)]), 4)
        finally:
            for k, v in [("XAI_API_KEY", old_x), ("GROK_API_KEY", old_g), ("ANTHROPIC_API_KEY", old_a), ("NOUS_API_KEY", old_n)]:
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
