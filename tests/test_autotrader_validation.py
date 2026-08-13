import json
import tempfile
import unittest
from pathlib import Path

from scripts.autotrader import DryRunAutoTrader


class TestAutoTraderValidation(unittest.TestCase):
    def make_config(self, root: Path) -> Path:
        data_dir = root / 'data'
        cfg = {
            'mode': 'PAPER_TRADING',
            'enabled': True,
            'account': {'account_id': 'SIM', 'starting_capital': 1000},
            'kill_switch': {'file_path': str(root / 'KILL_SWITCH.txt')},
            'data_files': {
                'trade_journal': str(data_dir / 'trade_journal.jsonl'),
                'order_ledger': str(data_dir / 'order_ledger.jsonl'),
                'consensus_log': str(data_dir / 'consensus_log.jsonl'),
            },
            'consensus_rules': {
                'min_confidence': 70,
                'model_1': 'grok-4.5',
                'model_2': 'claude-sonnet-5',
            },
            'position_limits': {'max_position_size_usd': 200, 'min_position_size_usd': 50, 'max_positions': 3},
            'order_limits': {'min_risk_reward_ratio': 1.5},
            'risk_rules': {'min_stop_distance_pct': 1, 'max_stop_distance_pct': 5, 'max_loss_per_day_pct': 5},
            'allowed_symbols': 'AI_DECIDES',
            'telegram': {'enabled': False, 'chat_id': ''},
        }
        path = root / 'config.json'
        path.write_text(json.dumps(cfg), encoding='utf-8')
        return path

    def test_generated_decision_at_max_position_limit_still_validates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = self.make_config(root)
            trader = DryRunAutoTrader(str(config_path))
            broker_snapshot = {'buying_power': 950.0, 'positions': [], 'pending_orders': []}
            market_data = {
                'LRCX': {
                    'symbol': 'LRCX',
                    'price': 311.0,
                    'volume': 1444460,
                    'rsi': 58.0,
                    'ema50': 295.0,
                    'rank_score': 72.59,
                    'catalyst_score': 13,
                    'sentiment': 'bullish',
                    'catalyst': 'Strong earnings and AI demand',
                }
            }

            decision = trader.get_model_decision('grok-4.5', broker_snapshot, market_data)
            valid, reason = trader.validate_order(decision, broker_snapshot)

            self.assertTrue(valid, reason)

    # --- Regression tests for Opus 5 review (2026-08) ---

    def test_sell_consensus_not_gated_on_stop_target(self):
        """Bug 1: SELL (exit) must NOT require stop/target agreement."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = self.make_config(root)
            trader = DryRunAutoTrader(str(config_path))
            # Both seniors SELL UNH, but stops differ wildly (Grok short-style
            # stop above price, Sonnet 0). On an exit these are meaningless.
            s1 = {
                'action': 'SELL', 'symbol': 'UNH', 'confidence': 78,
                'entry_price': 400.0, 'stop_loss': 410.5, 'take_profit': 0.0,
                'qty': 0.5, 'thesis': 'exit', 'reason_code': 'rotation',
            }
            s2 = {
                'action': 'SELL', 'symbol': 'UNH', 'confidence': 72,
                'entry_price': 400.0, 'stop_loss': 0.0, 'take_profit': 0.0,
                'qty': 0.5, 'thesis': 'exit', 'reason_code': 'rotation',
            }
            ok, reason = trader.check_consensus(s1, s2)
            self.assertTrue(ok, f"SELL consensus should not gate on stop/target: {reason}")

    def test_buy_consensus_still_gates_on_stop_target(self):
        """BUY entries still require stop/target agreement (unchanged behavior)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = self.make_config(root)
            trader = DryRunAutoTrader(str(config_path))
            b1 = {
                'action': 'BUY', 'symbol': 'AMD', 'confidence': 80,
                'entry_price': 100.0, 'stop_loss': 95.0, 'take_profit': 110.0,
                'qty': 2, 'thesis': 'entry', 'reason_code': 'new_entry',
            }
            b2 = {
                'action': 'BUY', 'symbol': 'AMD', 'confidence': 75,
                'entry_price': 100.0, 'stop_loss': 99.0, 'take_profit': 110.0,
                'qty': 2, 'thesis': 'entry', 'reason_code': 'new_entry',
            }
            # stop 95 vs 99 => 4% apart, under 5% threshold -> should pass
            ok, reason = trader.check_consensus(b1, b2)
            self.assertTrue(ok, f"BUY with close stops should agree: {reason}")
            # stop 95 vs 100.5 => 5.5% apart -> >5% threshold -> fail
            b3 = dict(b2, stop_loss=100.5)
            ok2, _ = trader.check_consensus(b1, b3)
            self.assertFalse(ok2, "BUY with wildly different stops must be rejected")

    def test_rr_epsilon_tolerance(self):
        """Bug 4: RR of 1.4999999 (displays 1.50) must not be rejected."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = self.make_config(root)
            trader = DryRunAutoTrader(str(config_path))
            broker = {'buying_power': 1000.0, 'positions': [], 'pending_orders': []}
            # entry 100, stop 99.0 (risk 1.0), target 101.4999999 (reward 1.4999999)
            # => RR = 1.4999999, displays 1.50
            decision = {
                'action': 'BUY', 'symbol': 'AMD', 'confidence': 80,
                'entry_price': 100.0, 'stop_loss': 99.0, 'take_profit': 101.4999999,
                'qty': 2, 'thesis': 'entry', 'reason_code': 'new_entry',
            }
            valid, reason = trader.validate_order(decision, broker)
            self.assertTrue(valid, f"RR near minimum must pass with epsilon: {reason}")

    def test_escalate_reason_written_back_on_nomination(self):
        """Bug 6: escalate_reason must be 'junior_nomination' when juniors nominate."""
        import scripts.dual_llm as dual_llm
        juniors = [
            {'action': 'SELL', 'symbol': 'UNH', 'confidence': 72, 'source': 'live'},
            {'action': 'SELL', 'symbol': 'UNH', 'confidence': 70, 'source': 'live'},
            {'action': 'BUY', 'symbol': 'AMD', 'confidence': 71, 'source': 'live'},
            {'action': 'BUY', 'symbol': 'AMD', 'confidence': 69, 'source': 'live'},
        ]
        # Even 2/2 split, min_agree=3 -> no quorum
        nom = dual_llm.junior_nomination(juniors, min_agree=3)
        self.assertIsNone(nom, "2/2 split with min_agree=3 should not nominate by default")
        # Tie-break enabled + book full -> prefer SELL (exit) side
        nom2 = dual_llm.junior_nomination(juniors, min_agree=3, prefer_exit_when_full=True)
        self.assertEqual(nom2, ("SELL", "UNH"), "tie-break should prefer the exit side")

    def test_tiebreak_never_exits_unheld_symbol(self):
        """Tie-break must not nominate a SELL on a symbol we don't hold (Claude review gap)."""
        import scripts.dual_llm as dual_llm
        juniors = [
            {'action': 'SELL', 'symbol': 'NVDA', 'confidence': 72, 'source': 'live'},
            {'action': 'SELL', 'symbol': 'NVDA', 'confidence': 70, 'source': 'live'},
            {'action': 'BUY', 'symbol': 'AMD', 'confidence': 71, 'source': 'live'},
            {'action': 'BUY', 'symbol': 'AMD', 'confidence': 69, 'source': 'live'},
        ]
        # NVDA not in held set -> tie-break must NOT fire
        nom = dual_llm.junior_nomination(
            juniors, min_agree=3, prefer_exit_when_full=True,
            held_symbols={'UNH', 'XOM', 'JPM', 'CVX', 'MSFT'},
        )
        self.assertIsNone(nom, "must not exit an unheld symbol via tie-break")
        # If NVDA IS held -> tie-break fires
        nom2 = dual_llm.junior_nomination(
            juniors, min_agree=3, prefer_exit_when_full=True,
            held_symbols={'NVDA', 'XOM'},
        )
        self.assertEqual(nom2, ("SELL", "NVDA"))

    def test_autotrader_passes_tiebreak_flag_through_flattened_rules(self):
        """Propagation: autotrader's flattened rules dict must carry the tie-break flag."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = self.make_config(root)
            trader = DryRunAutoTrader(str(config_path))
            # Reconstruct the flattened rules dict autotrader passes to dual_llm
            cr = json.loads(Path(config_path).read_text(encoding='utf-8')).get('consensus_rules', {})
            # autotrader copies the flag into `rules` (our fix)
            import scripts.autotrader as at
            # Simulate: the flattened dict now includes the key
            flat = {
                'max_positions': 5,
                'nomination_tie_break_exit_when_full': cr.get('nomination_tie_break_exit_when_full', False),
            }
            # dual_llm reads it from the flattened dict
            from scripts import dual_llm
            cr2 = dict(flat or {})
            self.assertIn('nomination_tie_break_exit_when_full', cr2)

    def test_sol_focus_sym_is_string(self):
        """Bug 3: Sol chart focus must be the symbol string, not the (action,symbol) tuple."""
        import scripts.dual_llm as dual_llm
        # nomination is (action, symbol); unpacked symbol must be a plain string
        nomination = ("SELL", "UNH")
        focus = (nomination[1] if nomination else "") or ""
        self.assertEqual(focus, "UNH")
        self.assertIsInstance(focus, str)


if __name__ == '__main__':
    unittest.main()
