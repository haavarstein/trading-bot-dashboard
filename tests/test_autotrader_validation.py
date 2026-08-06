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


if __name__ == '__main__':
    unittest.main()
