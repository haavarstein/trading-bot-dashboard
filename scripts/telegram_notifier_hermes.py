#!/usr/bin/env python3
"""
Hermes-Integrated Telegram Notifier
Uses Hermes cron job delivery system for reliable Telegram messages
"""

import json
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional

class HermesTelegramNotifier:
    def __init__(self, config_path: str = "./config/autonomy_config.json"):
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        self.enabled = config.get('telegram', {}).get('enabled', True)
        self.chat_id = config.get('telegram', {}).get('chat_id')
        
        self.notify_on_trade = config['telegram'].get('notify_on_trade', True)
        self.notify_on_disagreement = config['telegram'].get('notify_on_disagreement', True)
        self.notify_on_blocker = config['telegram'].get('notify_on_blocker', True)
        self.notify_on_error = config['telegram'].get('notify_on_error', True)
    
    def send_via_hermes(self, message: str) -> bool:
        """Send message via Hermes cron job (most reliable method)"""
        if not self.enabled or not self.chat_id:
            print(f"[Telegram Disabled] {message}")
            return False
        
        try:
            # Create a one-shot cron job that delivers the message
            cmd = [
                'hermes', 'cronjob', 'create',
                '--name', f'bot-notification-{datetime.now().timestamp()}',
                '--schedule', '1m',
                '--repeat', '1',
                '--deliver', f'telegram:{self.chat_id}',
                '--prompt', message
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except Exception as e:
            print(f"Hermes send error: {e}")
            print(f"[Fallback Print] {message}")
            return False
    
    def notify_trade_signal(self, action: str, symbol: str, price: float, 
                           qty: int, stop: float, target: float, 
                           confidence: int, thesis: str, dry_run: bool = True):
        """Notify about a trade signal"""
        if not self.notify_on_trade:
            return
        
        mode_emoji = "🧪" if dry_run else "💰"
        action_emoji = "🟢" if action == "BUY" else "🔴"
        
        risk = abs(price - stop) * qty
        reward = abs(target - price) * qty
        rr_ratio = reward / risk if risk > 0 else 0
        
        message = f"""{mode_emoji} {'DRY RUN' if dry_run else 'LIVE'} TRADE {action_emoji}

{action} {qty} {symbol} @ ${price:.2f}
Position: ${price * qty:.2f}

Stop: ${stop:.2f} ({((stop-price)/price*100):.2f}%)
Target: ${target:.2f} ({((target-price)/price*100):.2f}%)
R:R: {rr_ratio:.2f}

Confidence: {confidence}%
Thesis: {thesis}

{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"""
        
        self.send_via_hermes(message)
    
    def notify_disagreement(self, model1_decision: Dict, model2_decision: Dict, reason: str):
        """Notify about model disagreement"""
        if not self.notify_on_disagreement:
            return
        
        message = f"""⚠️ MODEL DISAGREEMENT - BLOCKED

Model 1: {model1_decision.get('action')} {model1_decision.get('symbol', 'N/A')} ({model1_decision.get('confidence', 0)}%)

Model 2: {model2_decision.get('action')} {model2_decision.get('symbol', 'N/A')} ({model2_decision.get('confidence', 0)}%)

Reason: {reason}

{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"""
        
        self.send_via_hermes(message)
    
    def notify_blocker(self, blocker_type: str, details: str):
        """Notify about validation blocker"""
        if not self.notify_on_blocker:
            return
        
        message = f"""🛑 TRADE BLOCKED - {blocker_type.upper()}

{details}

{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"""
        
        self.send_via_hermes(message)
    
    def notify_error(self, error_type: str, error_msg: str):
        """Notify about system error"""
        if not self.notify_on_error:
            return
        
        message = f"""❌ SYSTEM ERROR - {error_type.upper()}

{error_msg}

{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"""
        
        self.send_via_hermes(message)
    
    def notify_status(self, status: str, details: str = ""):
        """Send general status update"""
        emoji = {
            "started": "🚀",
            "stopped": "🛑",
            "paused": "⏸️",
            "resumed": "▶️",
            "kill_switch": "🔴"
        }.get(status.lower(), "ℹ️")
        
        message = f"""{emoji} BOT: {status.upper()}

{details}

{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"""
        
        self.send_via_hermes(message)


if __name__ == "__main__":
    # Test notification
    notifier = HermesTelegramNotifier()
    notifier.notify_status("STARTED", "Trading bot Hermes integration test - notifications via cron job delivery")
