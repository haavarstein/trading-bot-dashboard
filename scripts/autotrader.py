#!/usr/bin/env python3
"""
DRY-RUN Trading Bot - Main Orchestrator
Implements dual-model consensus + validation + Telegram notifications
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from telegram_notifier import TelegramNotifier
except ImportError:
    print("Warning: telegram_notifier not found, notifications disabled")
    TelegramNotifier = None


class DryRunAutoTrader:
    def __init__(self, config_path: str = "./config/autonomy_config.json"):
        self.config = self._load_config(config_path)
        self.mode = self.config['mode']
        self.enabled = self.config['enabled']
        
        # Initialize Telegram
        self.telegram = TelegramNotifier(config_path) if TelegramNotifier else None
        
        # Data paths
        self.trade_journal_path = self.config['data_files']['trade_journal']
        self.order_ledger_path = self.config['data_files']['order_ledger']
        self.consensus_log_path = self.config['data_files']['consensus_log']
        
        # Ensure data files exist
        self._init_data_files()
        
    def _load_config(self, config_path: str) -> Dict:
        """Load and validate configuration"""
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Validate critical fields
        assert config['mode'] in ['DRY_RUN', 'PAPER_TRADING', 'LIVE'], "Mode must be DRY_RUN, PAPER_TRADING, or LIVE"
        assert config['enabled'] is False or config['mode'] in ['PAPER_TRADING', 'LIVE'], "Cannot enable DRY_RUN mode"
        
        return config
    
    def _init_data_files(self):
        """Initialize JSONL data files if they don't exist"""
        for path in [self.trade_journal_path, self.order_ledger_path, self.consensus_log_path]:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                with open(path, 'w') as f:
                    pass  # Create empty file
    
    def check_kill_switch(self) -> bool:
        """Check if kill switch file exists"""
        kill_switch_path = self.config['kill_switch']['file_path']
        if os.path.exists(kill_switch_path):
            if self.telegram:
                self.telegram.notify_status(
                    "KILL_SWITCH", 
                    f"Kill switch detected at {kill_switch_path}. All trading halted."
                )
            print(f"🔴 KILL SWITCH ACTIVE: {kill_switch_path}")
            return True
        return False
    
    def log_to_ledger(self, ledger_path: str, entry: Dict):
        """Append entry to JSONL ledger"""
        entry['timestamp'] = datetime.now(timezone.utc).isoformat()
        with open(ledger_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    
    def get_mock_broker_snapshot(self) -> Dict:
        """
        Simulates reading from IBKR MCP.
        In LIVE mode, this would call actual IBKR MCP tools.
        """
        return {
            "account_id": self.config['account']['account_id'],
            "buying_power": 950.00,  # Simulated
            "positions": [
                {
                    "symbol": "TSLA",
                    "qty": 3,
                    "avg_cost": 320.00,
                    "current_price": 321.55,
                    "unrealized_pnl": 4.65
                }
            ],
            "pending_orders": [],
            "mode": "DRY_RUN"
        }
    
    def get_mock_market_data(self, symbol: str) -> Dict:
        """
        Simulates market data from TradingView MCP.
        In LIVE mode, this would call actual TradingView MCP tools.
        """
        mock_data = {
            "TSLA": {"price": 321.55, "volume": 27820813, "rsi": 37.53, "ema50": 335.00},
            "NVDA": {"price": 125.50, "volume": 45000000, "rsi": 55.20, "ema50": 122.00},
            "AAPL": {"price": 227.00, "volume": 35000000, "rsi": 48.30, "ema50": 225.00},
            "SPY": {"price": 565.00, "volume": 50000000, "rsi": 52.00, "ema50": 560.00},
            "QQQ": {"price": 485.00, "volume": 30000000, "rsi": 54.00, "ema50": 480.00},
        }
        return mock_data.get(symbol, {"price": 0, "volume": 0, "rsi": 50, "ema50": 0})
    
    def get_model_decision(self, model_name: str, broker_snapshot: Dict, market_data: Dict) -> Dict:
        """
        Simulates asking Grok or Claude for a decision.
        In LIVE mode, this would call actual LLM APIs with market context.
        
        Returns: {
            "action": "BUY" | "SELL" | "HOLD",
            "symbol": str,
            "confidence": int (0-100),
            "stop_loss": float,
            "take_profit": float,
            "thesis": str
        }
        """
        # DRY-RUN: Return simulated decision
        # In production, this calls Hermes with the model and evidence bundle
        
        return {
            "model": model_name,
            "action": "BUY",
            "symbol": "NVDA",
            "confidence": 75,
            "entry_price": 125.50,
            "stop_loss": 122.00,
            "take_profit": 132.00,
            "qty": 1,
            "thesis": "AI sector momentum, RSI neutral, above 50 EMA",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    def check_consensus(self, decision1: Dict, decision2: Dict) -> Tuple[bool, Optional[str]]:
        """
        Check if two model decisions agree.
        Returns: (consensus_reached, reason_if_blocked)
        """
        # Must agree on action
        if decision1['action'] != decision2['action']:
            return False, f"Action mismatch: {decision1['action']} vs {decision2['action']}"
        
        # Must agree on symbol
        if decision1['symbol'] != decision2['symbol']:
            return False, f"Symbol mismatch: {decision1['symbol']} vs {decision2['symbol']}"
        
        # Both must meet minimum confidence
        min_confidence = self.config['consensus_rules']['min_confidence']
        if decision1['confidence'] < min_confidence:
            return False, f"Model 1 confidence {decision1['confidence']}% < {min_confidence}%"
        if decision2['confidence'] < min_confidence:
            return False, f"Model 2 confidence {decision2['confidence']}% < {min_confidence}%"
        
        # Stops and targets should be materially similar (within 5%)
        if abs(decision1['stop_loss'] - decision2['stop_loss']) / decision1['entry_price'] > 0.05:
            return False, f"Stop loss mismatch: ${decision1['stop_loss']} vs ${decision2['stop_loss']}"
        
        if abs(decision1['take_profit'] - decision2['take_profit']) / decision1['entry_price'] > 0.05:
            return False, f"Take profit mismatch: ${decision1['take_profit']} vs ${decision2['take_profit']}"
        
        return True, None
    
    def validate_order(self, decision: Dict, broker_snapshot: Dict) -> Tuple[bool, Optional[str]]:
        """
        Deterministic validation of agreed decision.
        Even with consensus, code enforces all risk rules.
        """
        symbol = decision['symbol']
        action = decision['action']
        entry = decision['entry_price']
        stop = decision['stop_loss']
        target = decision['take_profit']
        qty = decision['qty']
        
        # Check kill switch first
        if self.check_kill_switch():
            return False, "Kill switch active"
        
        # Check symbol whitelist (if AI_DECIDES, skip whitelist check)
        allowed = self.config.get('allowed_symbols', 'AI_DECIDES')
        if allowed != 'AI_DECIDES' and symbol not in allowed:
            return False, f"{symbol} not in allowed symbols"
        
        # Check buying power
        position_value = entry * qty
        if position_value > broker_snapshot['buying_power']:
            return False, f"Insufficient buying power: ${broker_snapshot['buying_power']:.2f} < ${position_value:.2f}"
        
        # Check position size limits
        if position_value > self.config['position_limits']['max_position_size_usd']:
            return False, f"Position size ${position_value:.2f} > max ${self.config['position_limits']['max_position_size_usd']}"
        
        if position_value < self.config['position_limits']['min_position_size_usd']:
            return False, f"Position size ${position_value:.2f} < min ${self.config['position_limits']['min_position_size_usd']}"
        
        # Check risk/reward ratio
        risk = abs(entry - stop) * qty
        reward = abs(target - entry) * qty
        rr_ratio = reward / risk if risk > 0 else 0
        
        min_rr = self.config['order_limits']['min_risk_reward_ratio']
        if rr_ratio < min_rr:
            return False, f"Risk/Reward {rr_ratio:.2f} < minimum {min_rr}"
        
        # Check stop loss requirements
        if not stop or stop == 0:
            return False, "Stop loss required but not provided"
        
        if not target or target == 0:
            return False, "Take profit required but not provided"
        
        # Check stop distance
        stop_distance_pct = abs(entry - stop) / entry * 100
        if stop_distance_pct < self.config['risk_rules']['min_stop_distance_pct']:
            return False, f"Stop too tight: {stop_distance_pct:.2f}% < {self.config['risk_rules']['min_stop_distance_pct']}%"
        
        if stop_distance_pct > self.config['risk_rules']['max_stop_distance_pct']:
            return False, f"Stop too wide: {stop_distance_pct:.2f}% > {self.config['risk_rules']['max_stop_distance_pct']}%"
        
        # All validations passed
        return True, None
    
    def execute_trade(self, decision: Dict, dry_run: bool = True):
        """
        Execute approved trade (or simulate in dry-run mode).
        """
        symbol = decision['symbol']
        action = decision['action']
        qty = decision['qty']
        entry = decision['entry_price']
        stop = decision['stop_loss']
        target = decision['take_profit']
        
        order_entry = {
            "mode": "DRY_RUN" if dry_run else "LIVE",
            "action": action,
            "symbol": symbol,
            "qty": qty,
            "entry_price": entry,
            "stop_loss": stop,
            "take_profit": target,
            "confidence": decision['confidence'],
            "thesis": decision['thesis'],
            "status": "SIMULATED" if dry_run else "PENDING",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Log to order ledger
        self.log_to_ledger(self.order_ledger_path, order_entry)
        
        # Send Telegram notification
        if self.telegram:
            self.telegram.notify_trade_signal(
                action=action,
                symbol=symbol,
                price=entry,
                qty=qty,
                stop=stop,
                target=target,
                confidence=decision['confidence'],
                thesis=decision['thesis'],
                dry_run=dry_run
            )
        
        print(f"\n{'[DRY-RUN]' if dry_run else '[LIVE]'} {action} {qty} {symbol} @ ${entry:.2f}")
        print(f"  Stop: ${stop:.2f} | Target: ${target:.2f}")
        print(f"  Thesis: {decision['thesis']}")
    
    def run_trading_cycle(self):
        """
        Main trading cycle - called by cron job.
        """
        print(f"\n{'='*60}")
        print(f"Trading Bot Cycle - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"Mode: {self.mode} | Enabled: {self.enabled}")
        print(f"{'='*60}\n")
        
        # Check kill switch
        if self.check_kill_switch():
            return
        
        # Step 1: Read broker snapshot (via IBKR MCP in production)
        broker_snapshot = self.get_mock_broker_snapshot()
        print(f"📊 Broker Snapshot: ${broker_snapshot['buying_power']:.2f} buying power")
        
        # Step 2: Get independent decisions from both models
        model1_name = self.config['consensus_rules']['model_1']
        model2_name = self.config['consensus_rules']['model_2']
        
        print(f"🤖 Requesting decisions from {model1_name} and {model2_name}...")
        
        # Get market context (from Alpha Radar candidates if AI decides)
        allowed = self.config.get('allowed_symbols', 'AI_DECIDES')
        if allowed == 'AI_DECIDES':
            # Load candidates from Alpha Radar
            candidates_file = './data/candidates.json'
            if os.path.exists(candidates_file):
                with open(candidates_file, 'r') as f:
                    radar_data = json.load(f)
                    candidates = [c['symbol'] for c in radar_data.get('candidates', [])]
            else:
                candidates = ['TSLA', 'NVDA', 'AAPL']  # Fallback
        else:
            candidates = allowed
        
        market_context = {sym: self.get_mock_market_data(sym) for sym in candidates}
        
        decision1 = self.get_model_decision(model1_name, broker_snapshot, market_context)
        decision2 = self.get_model_decision(model2_name, broker_snapshot, market_context)
        
        print(f"  {model1_name}: {decision1['action']} {decision1['symbol']} @ {decision1['confidence']}%")
        print(f"  {model2_name}: {decision2['action']} {decision2['symbol']} @ {decision2['confidence']}%")
        
        # Log consensus attempt
        consensus_entry = {
            "model1": decision1,
            "model2": decision2,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Step 3: Check consensus
        consensus, reason = self.check_consensus(decision1, decision2)
        consensus_entry["consensus"] = consensus
        consensus_entry["reason"] = reason
        
        if not consensus:
            print(f"\n🚫 CONSENSUS BLOCKED: {reason}")
            self.log_to_ledger(self.consensus_log_path, consensus_entry)
            
            if self.telegram:
                self.telegram.notify_disagreement(decision1, decision2, reason)
            return
        
        print(f"\n✅ CONSENSUS REACHED: {decision1['action']} {decision1['symbol']}")
        
        # Step 4: Deterministic validation
        valid, blocker_reason = self.validate_order(decision1, broker_snapshot)
        consensus_entry["validation"] = {"valid": valid, "reason": blocker_reason}
        self.log_to_ledger(self.consensus_log_path, consensus_entry)
        
        if not valid:
            print(f"🛑 VALIDATION BLOCKED: {blocker_reason}")
            if self.telegram:
                self.telegram.notify_blocker("VALIDATION_FAILED", blocker_reason)
            return
        
        print(f"✅ VALIDATION PASSED")
        
        # Step 5: Execute (determine if real or simulated)
        is_simulation = (self.mode in ['DRY_RUN', 'PAPER_TRADING'])
        self.execute_trade(decision1, dry_run=is_simulation)
        
        print(f"\n{'='*60}\n")


if __name__ == "__main__":
    try:
        trader = DryRunAutoTrader()
        trader.run_trading_cycle()
    except Exception as e:
        import traceback
        print(f"\n❌ ERROR: {e}")
        traceback.print_exc()
        
        # Try to send error notification
        try:
            notifier = TelegramNotifier()
            notifier.notify_error("AUTOTRADER_CRASH", str(e), traceback.format_exc())
        except:
            pass
