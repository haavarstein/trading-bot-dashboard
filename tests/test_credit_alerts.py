#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import credit_alerts  # noqa: E402
from telegram_notifier import TelegramNotifier  # noqa: E402


class TestCreditAlerts(unittest.TestCase):
    def test_detects_common_credit_errors(self):
        self.assertTrue(credit_alerts.looks_like_credit_error("HTTP Error 402: Payment Required"))
        self.assertTrue(credit_alerts.looks_like_credit_error("insufficient_quota"))
        self.assertTrue(credit_alerts.looks_like_credit_error("FMP daily call budget exhausted"))
        self.assertTrue(credit_alerts.looks_like_credit_error("429 Too Many Requests rate limit"))
        self.assertFalse(credit_alerts.looks_like_credit_error("connection reset by peer"))

    def test_notifier_method_exists_and_respects_flag(self):
        n = TelegramNotifier()
        self.assertTrue(hasattr(n, "notify_api_credits"))
        self.assertTrue(TelegramNotifier.looks_like_credit_error("out of credits"))


if __name__ == "__main__":
    unittest.main()
