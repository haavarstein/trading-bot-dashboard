#!/usr/bin/env python3
"""
IBKR Trading Bot - Telegram Integration Module
Sends notifications to Telegram for all important events
"""

import json
import requests
from datetime import datetime
from typing import Dict, Any, Optional

class TelegramNotifier:
    def __init__(self, config_path: str = "./config/autonomy_config.json"):
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        self.enabled = config.get('telegram', {}).get('enabled', True)
        self.chat_id = config.get('telegram', {}).get('chat_id')
        self.bot_token = self._get_bot_token()
        
        self.notify_on_trade = config['telegram'].get('notify_on_trade', True)
        self.notify_on_disagreement = config['telegram'].get('notify_on_disagreement', True)
        self.notify_on_blocker = config['telegram'].get('notify_on_blocker', True)
        self.notify_on_error = config['telegram'].get('notify_on_error', True)
        
    def _get_bot_token(self) -> Optional[str]:
        """Get Telegram bot token from Hermes gateway config"""
        try:
            import os
            # Hermes gateway typically stores token in config
            gateway_config = os.path.expanduser('~/.hermes/gateway.yaml')
            if os.path.exists(gateway_config):
                import yaml
                with open(gateway_config, 'r') as f:
                    gw_config = yaml.safe_load(f)
                    return gw_config.get('telegram', {}).get('token')
        except Exception as e:
            print(f"Warning: Could not load Telegram token: {e}")
            return None
    
    def send_message(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send message to Telegram"""
        if not self.enabled or not self.bot_token or not self.chat_id:
            print(f"[Telegram Disabled] {message}")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode
            }
            response = requests.post(url, json=data, timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"Telegram send error: {e}")
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
        
        message = f"""
{mode_emoji} *{'DRY RUN' if dry_run else 'LIVE'}* TRADE SIGNAL {action_emoji}

*Action:* {action}
*Symbol:* {symbol}
*Price:* ${price:.2f}
*Quantity:* {qty} shares
*Position Size:* ${price * qty:.2f}

📊 *Risk Management:*
• Stop Loss: ${stop:.2f} ({((stop-price)/price*100):.2f}%)
• Take Profit: ${target:.2f} ({((target-price)/price*100):.2f}%)
• Risk: ${risk:.2f}
• Reward: ${reward:.2f}
• R:R Ratio: {rr_ratio:.2f}

🎯 *Confidence:* {confidence}%

💡 *Thesis:* {thesis}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
"""
        self.send_message(message.strip())
    
    def notify_disagreement(self, model1_decision: Dict, model2_decision: Dict, reason: str):
        """Notify about model disagreement"""
        if not self.notify_on_disagreement:
            return
        
        message = f"""
⚠️ *MODEL DISAGREEMENT - TRADE BLOCKED*

*Model 1:* {model1_decision.get('action')} {model1_decision.get('symbol', 'N/A')}
*Confidence:* {model1_decision.get('confidence', 0)}%

*Model 2:* {model2_decision.get('action')} {model2_decision.get('symbol', 'N/A')}
*Confidence:* {model2_decision.get('confidence', 0)}%

🚫 *Reason:* {reason}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
"""
        self.send_message(message.strip())
    
    def notify_blocker(self, blocker_type: str, details: str):
        """Notify about validation blocker"""
        if not self.notify_on_blocker:
            return
        
        message = f"""
🛑 *TRADE BLOCKED - {blocker_type.upper()}*

{details}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
"""
        self.send_message(message.strip())
    
    def notify_error(self, error_type: str, error_msg: str, stack_trace: Optional[str] = None):
        """Notify about system error"""
        if not self.notify_on_error:
            return
        
        message = f"""
❌ *SYSTEM ERROR - {error_type.upper()}*

{error_msg}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
"""
        if stack_trace:
            message += f"\n```\n{stack_trace[:500]}\n```"
        
        self.send_message(message.strip())
    
    def notify_daily_summary(self, summary: Dict):
        """Send daily summary"""
        message = f"""
📈 *DAILY TRADING SUMMARY*

*Trades Executed:* {summary.get('trades_executed', 0)}
*Win Rate:* {summary.get('win_rate', 0):.1f}%
*P&L:* ${summary.get('pnl', 0):.2f}
*Account Value:* ${summary.get('account_value', 0):.2f}

*Model Consensus Rate:* {summary.get('consensus_rate', 0):.1f}%
*Trades Blocked:* {summary.get('trades_blocked', 0)}

⏰ {datetime.utcnow().strftime('%Y-%m-%d')}
"""
        self.send_message(message.strip())
    
    def notify_status(self, status: str, details: str = ""):
        """Send general status update"""
        emoji = {
            "started": "🚀",
            "stopped": "🛑",
            "paused": "⏸️",
            "resumed": "▶️",
            "kill_switch": "🔴"
        }.get(status.lower(), "ℹ️")
        
        message = f"""
{emoji} *BOT STATUS: {status.upper()}*

{details}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
"""
        self.send_message(message.strip())


if __name__ == "__main__":
    # Test notification
    notifier = TelegramNotifier()
    notifier.notify_status("STARTED", "Trading bot initialization test")
    
    # Test dry-run trade signal
    notifier.notify_trade_signal(
        action="BUY",
        symbol="TSLA",
        price=321.55,
        qty=3,
        stop=315.00,
        target=335.00,
        confidence=75,
        thesis="RSI oversold + bullish divergence, testing $320 support",
        dry_run=True
    )
