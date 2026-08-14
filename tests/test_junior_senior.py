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
    """Production gate is junior_majority_vote + junior_nomination."""

    def _junior(self, action, conf, source="live"):
        return {
            "action": action, "symbol": "CASH" if action == "HOLD" else "MSFT",
            "confidence": conf, "source": source,
            "entry_price": 0, "stop_loss": 0, "take_profit": 0, "qty": 0,
            "thesis": "x", "reason_code": "hold",
        }

    def test_hold_majority_stays_junior(self):
        # All HOLD above floor, 3-of-4 -> agreed HOLD, no escalation
        js = [self._junior("HOLD", 62), self._junior("HOLD", 68),
              self._junior("HOLD", 58), self._junior("HOLD", 64)]
        ok, reason, _ = dual_llm.junior_majority_vote(js, hold_floor=55, min_agree=3)
        self.assertTrue(ok, reason)
        # no trade intent -> no nomination either
        self.assertIsNone(dual_llm.junior_nomination(js, min_agree=3))

    def test_buy_escalates_even_if_juniors_agree(self):
        # Any live BUY intent -> escalate regardless of HOLD quorum
        js = [self._junior("HOLD", 60), self._junior("BUY", 80),
              self._junior("HOLD", 65), self._junior("HOLD", 62)]
        ok, reason, _ = dual_llm.junior_majority_vote(js, hold_floor=55, min_agree=3)
        self.assertFalse(ok)
        self.assertIn("junior_trade_intent", reason)
        # single BUY vote is below the 3-vote nomination quorum -> no pin yet
        self.assertIsNone(dual_llm.junior_nomination(js, min_agree=3))

    def test_buy_quorum_surfaces_as_nomination(self):
        # 3 live juniors on the SAME (BUY, symbol) -> a real nomination
        js = [self._junior("BUY", 80), self._junior("BUY", 82),
              self._junior("BUY", 78), self._junior("HOLD", 62)]
        ok, reason, _ = dual_llm.junior_majority_vote(js, hold_floor=55, min_agree=3)
        self.assertFalse(ok)
        self.assertIn("junior_trade_intent", reason)
        self.assertEqual(dual_llm.junior_nomination(js, min_agree=3), ("BUY", "MSFT"))

    def test_junior_split_escalates(self):
        # No HOLD quorum (2/2 split) -> escalation path, no nomination by default
        js = [self._junior("SELL", 72), self._junior("BUY", 75),
              self._junior("SELL", 70), self._junior("BUY", 69)]
        ok, reason, _ = dual_llm.junior_majority_vote(js, hold_floor=55, min_agree=3)
        self.assertFalse(ok)
        # split -> no single-pair quorum -> no nomination (tie-break is separate)
        self.assertIsNone(dual_llm.junior_nomination(js, min_agree=3))


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
