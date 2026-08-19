#!/usr/bin/env python3
"""Unit tests for senior output-token cap threading (no live API calls)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dual_llm  # noqa: E402


def _decision(model, action="BUY", symbol="MSFT", conf=80):
    return {
        "model": model, "action": action, "symbol": symbol, "confidence": conf,
        "source": "live", "entry_price": 0, "stop_loss": 0, "take_profit": 0,
        "qty": 0, "thesis": "x", "reason_code": "new_entry",
    }


class TestSeniorMaxTokens(unittest.TestCase):
    def _run(self, rules):
        broker = {"cash": 1000.0, "equity": 1000.0, "positions": []}
        market = {"MSFT": {"price": 500.0, "rank_score": 70, "catalyst_score": 5,
                           "sentiment": "bullish", "rsi": 55, "catalyst": "x"}}
        calls = []

        orig = dual_llm.get_live_or_fallback
        def fake(model_name, broker_, market_, rules_, fallback_fn,
                 effort=None, directive=None, max_tokens=None):
            calls.append({"model": model_name, "directive": directive, "max_tokens": max_tokens})
            return _decision(model_name, action="BUY" if directive else "BUY", symbol="MSFT")

        dual_llm.get_live_or_fallback = fake
        try:
            out = dual_llm.run_junior_senior_consensus(
                broker, market, rules, lambda *a: _decision("fb"),
                lambda *ds: (True, None, ds[0]),
            )
        finally:
            dual_llm.get_live_or_fallback = orig
        return calls, out

    def test_senior_calls_get_550_juniors_default(self):
        rules = {
            "junior_enabled": True,
            "junior_models": ["grok-4.3", "claude-haiku-4-5", "deepseek-v4-flash", "grok-build-0.1"],
            "junior_min_agree": 3,
            "model_1": "grok-4.5", "model_2": "claude-sonnet-5", "model_3": "claude-opus-5",
            "senior_max_tokens": 550,
        }
        calls, out = self._run(rules)
        # 4 juniors + 3 seniors
        self.assertEqual(len(calls), 7)
        self.assertEqual(out["tier"], "senior_escalated")
        seniors = [c for c in calls if c["directive"] == "BUY MSFT"]
        juniors = [c for c in calls if c["directive"] is None]
        self.assertEqual(len(seniors), 3)
        self.assertEqual(len(juniors), 4)
        # seniors capped at 550; juniors untouched (None -> provider default)
        self.assertTrue(all(c["max_tokens"] == 550 for c in seniors))
        self.assertTrue(all(c["max_tokens"] is None for c in juniors))

    def test_default_is_550_when_config_missing(self):
        rules = {
            "junior_enabled": True,
            "junior_models": ["grok-4.3", "claude-haiku-4-5", "deepseek-v4-flash", "grok-build-0.1"],
            "junior_min_agree": 3,
            "model_1": "grok-4.5", "model_2": "claude-sonnet-5", "model_3": "claude-opus-5",
        }
        calls, _ = self._run(rules)
        seniors = [c for c in calls if c["directive"] == "BUY MSFT"]
        self.assertTrue(seniors)
        self.assertTrue(all(c["max_tokens"] == 550 for c in seniors))


if __name__ == "__main__":
    unittest.main()
