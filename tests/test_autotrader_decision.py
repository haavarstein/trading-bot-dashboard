import json
import tempfile
import unittest
from pathlib import Path

from scripts.autotrader import DryRunAutoTrader


class TestAutoTraderDecision(unittest.TestCase):
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
                'model_1': 'grok-beta',
                'model_2': 'claude-sonnet-4-5',
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

    def test_model_decision_chooses_top_ranked_candidate_not_hardcoded_symbol(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = self.make_config(root)
            trader = DryRunAutoTrader(str(config_path))
            broker_snapshot = {'buying_power': 950.0, 'positions': [], 'pending_orders': []}
            market_data = {
                'LRCX': {
                    'symbol': 'LRCX',
                    'price': 309.79,
                    'volume': 42000000,
                    'rsi': 58.0,
                    'ema50': 295.0,
                    'rank_score': 72.27,
                    'sentiment': 'bullish',
                    'catalyst': 'Strong earnings and AI demand',
                },
                'TSLA': {
                    'symbol': 'TSLA',
                    'price': 320.28,
                    'volume': 27700000,
                    'rsi': 44.0,
                    'ema50': 335.0,
                    'rank_score': 47.16,
                    'sentiment': 'bullish',
                    'catalyst': 'Post-earnings valuation debate',
                },
            }

            decision = trader.get_model_decision('grok-beta', broker_snapshot, market_data)

            self.assertEqual(decision['symbol'], 'LRCX')
            self.assertNotEqual(decision['symbol'], 'NVDA')
            self.assertEqual(decision['action'], 'BUY')
            self.assertGreaterEqual(decision['confidence'], 70)


if __name__ == '__main__':
    unittest.main()
