#!/usr/bin/env python3
"""Dashboard equity curve: stored marks are truth; broken fill bursts stay out."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_dashboard_data as g  # noqa: E402


def _fill(ts, action, symbol, qty, price, **extra):
    row = {
        "timestamp": ts,
        "action": action,
        "symbol": symbol,
        "qty": qty,
        "price": price,
        "status": "FILLED_PAPER",
    }
    row.update(extra)
    return row


class TestDashboardEquityCurve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.patcher = patch.object(g, "DATA", self.data)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def _write_marks(self, rows):
        path = self.data / "equity_curve.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def _snap(self, started="2026-08-12T00:00:00+00:00", updated="2026-08-17T18:00:00+00:00",
              equity=1035.28, cash=30.01):
        return {
            "started_at": started,
            "updated_at": updated,
            "equity": equity,
            "cash": cash,
        }

    def test_schema_keys_unchanged(self):
        curve = g.build_equity_curve([], self._snap(), 1000.0)
        self.assertEqual(
            set(curve.keys()),
            {"currency", "start_equity", "latest_equity", "change", "change_pct", "points", "source"},
        )

    def test_excludes_fills_before_min_fill_date(self):
        fills = [
            _fill("2026-08-06T15:49:41.748264+00:00", "BUY", "ANET", 1.0, 193.1),
            _fill("2026-08-11T18:44:01.472745+00:00", "SELL", "PANW", 0.5, 382.0),
            _fill("2026-08-12T13:33:13.328443+00:00", "SELL", "LRCX", 0.65, 327.61),
            _fill("2026-08-14T15:00:00.000000+00:00", "SELL", "JPM", 0.5, 360.0),
        ]
        curve = g.build_equity_curve(fills, self._snap(), 1000.0)
        events = [p["event"] for p in curve["points"]]
        # pre-cutoff (Aug-06 / 08-11 / 08-12) fills excluded
        self.assertNotIn("BUY ANET", events)
        self.assertNotIn("SELL PANW", events)
        self.assertNotIn("SELL LRCX", events)
        # on/after cutoff (08-14) fill included
        self.assertIn("SELL JPM", events)
        self.assertIn("start", events)
        self.assertIn("mark", events)
        self.assertGreaterEqual(g.MIN_FILL_DATE, "2026-08-14")

    def test_broken_session_burst_does_not_spike(self):
        fills = []
        base = datetime(2026, 8, 14, 15, 12, 32, tzinfo=timezone.utc)
        for i in range(20):
            t0 = (base + timedelta(seconds=i * 4)).isoformat()
            t1 = (base + timedelta(seconds=i * 4, milliseconds=5)).isoformat()
            fills.append(_fill(t0, "BUY", "MSFT", 0.4, 500.0, cash_after=800.0))
            fills.append(
                _fill(
                    t1, "SELL", "MSFT", 0.4, 505.0, cash_after=1002.0,
                    hold_seconds=0, opened_at=t0, realized_pnl=2.0, reason="rotation",
                )
            )
        for i in range(12):
            t = (base + timedelta(seconds=2 + i * 4)).isoformat()
            fills.append(_fill(t, "BUY", "NVDA", 1.0, 300.0, cash_after=500.0))
        self._write_marks([
            {"t": "2026-08-13T16:00:00+00:00", "equity": 1035.28, "cash": 30.01, "event": "mark"},
            {"t": "2026-08-14T16:00:00+00:00", "equity": 1042.94, "cash": 28.50, "event": "mark"},
        ])
        curve = g.build_equity_curve(fills, self._snap(equity=1035.28), 1000.0)
        equities = [p["equity"] for p in curve["points"]]
        self.assertTrue(equities)
        self.assertLess(max(equities), 1100)
        self.assertGreaterEqual(max(equities), 1000)
        events = [p["event"] for p in curve["points"]]
        self.assertNotIn("BUY MSFT", events)
        self.assertNotIn("SELL MSFT", events)
        self.assertNotIn("BUY NVDA", events)
        self.assertIn(1042.94, equities)

    def test_same_day_dedupe_keeps_first_identical_fill(self):
        fills = [
            _fill("2026-08-15T15:00:00.001000+00:00", "BUY", "AMD", 1.0, 100.0, cash_after=900.0),
            _fill("2026-08-15T15:00:00.002000+00:00", "BUY", "AMD", 1.0, 100.0, cash_after=800.0),
            _fill("2026-08-15T15:00:01.003000+00:00", "BUY", "AMD", 1.0, 100.0, cash_after=700.0),
            _fill("2026-08-15T16:00:00.000000+00:00", "BUY", "AMD", 0.5, 110.0, cash_after=645.0),
        ]
        curve = g.build_equity_curve(fills, self._snap(updated="2026-08-15T17:00:00+00:00"), 1000.0)
        amd = [p for p in curve["points"] if p["event"] == "BUY AMD"]
        self.assertEqual(len(amd), 2)
        self.assertEqual(amd[0]["t"], "2026-08-15T15:00:00.001000+00:00")
        self.assertEqual(amd[1]["t"], "2026-08-15T16:00:00.000000+00:00")

    def test_valid_post_broken_fills_still_appear(self):
        fills = []
        base = datetime(2026, 8, 14, 15, 12, 32, tzinfo=timezone.utc)
        for i in range(5):
            t0 = (base + timedelta(seconds=i * 4)).isoformat()
            t1 = (base + timedelta(seconds=i * 4, milliseconds=6)).isoformat()
            fills.append(_fill(t0, "BUY", "MSFT", 0.4, 500.0))
            fills.append(_fill(t1, "SELL", "MSFT", 0.4, 505.0, hold_seconds=0, opened_at=t0))
        fills.append(_fill("2026-08-14T13:47:59.587989+00:00", "BUY", "AMZN", 0.75, 264.78, cash_after=30.01))
        fills.append(_fill("2026-08-14T17:03:34.242261+00:00", "SELL", "JPM", 0.55, 362.62, cash_after=229.45))
        fills.append(_fill("2026-08-17T14:18:11.775126+00:00", "BUY", "PANW", 0.53, 375.76, cash_after=50.0))
        curve = g.build_equity_curve(fills, self._snap(), 1000.0)
        events = [p["event"] for p in curve["points"]]
        self.assertIn("BUY AMZN", events)
        self.assertIn("SELL JPM", events)
        self.assertIn("BUY PANW", events)
        self.assertNotIn("BUY MSFT", events)
        self.assertLess(max(p["equity"] for p in curve["points"]), 1100)

    def test_stored_marks_win_over_fill_replay_at_same_timestamp(self):
        ts = "2026-08-13T12:00:00+00:00"
        self._write_marks([{"t": ts, "equity": 1042.94, "cash": 40.0, "event": "mark"}])
        # A fill at the same timestamp would reconstruct a huge book if replayed.
        fills = [_fill(ts, "BUY", "NVDA", 20.0, 300.0, cash_after=5.0)]
        curve = g.build_equity_curve(fills, self._snap(updated="2026-08-13T18:00:00+00:00"), 1000.0)
        at = [p for p in curve["points"] if p["t"] == ts]
        self.assertEqual(len(at), 1)
        self.assertEqual(at[0]["equity"], 1042.94)
        self.assertEqual(at[0]["event"], "mark")
        self.assertLess(max(p["equity"] for p in curve["points"]), 1100)


if __name__ == "__main__":
    unittest.main()
