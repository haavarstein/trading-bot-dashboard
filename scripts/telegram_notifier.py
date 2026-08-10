#!/usr/bin/env python3
"""
Paper trading bot - Telegram notifications.

Default policy: notify only on BUY/SELL fills (and hard errors).
HOLD / desk focus disagreements / routine blockers stay quiet.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


DEFAULT_DASHBOARD_URL = "https://trading-bot-delta-roan.vercel.app"


class TelegramNotifier:
    def __init__(self, config_path: str = "./config/autonomy_config.json"):
        path = Path(config_path)
        if not path.is_absolute():
            # prefer repo config next to scripts/
            cand = Path(__file__).resolve().parent.parent / "config" / "autonomy_config.json"
            if cand.exists():
                path = cand
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)

        tg = config.get("telegram") or {}
        self.enabled = bool(tg.get("enabled", True))
        self.chat_id = tg.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID") or "TELEGRAM_CHAT_ID_ENV"
        self.bot_token = self._get_bot_token()
        self.dashboard_url = (
            tg.get("dashboard_url")
            or os.environ.get("DASHBOARD_URL")
            or DEFAULT_DASHBOARD_URL
        )

        # Trades only by default
        self.notify_on_trade = bool(tg.get("notify_on_trade", True))
        self.notify_on_disagreement = bool(tg.get("notify_on_disagreement", False))
        self.notify_on_blocker = bool(tg.get("notify_on_blocker", False))
        self.notify_on_error = bool(tg.get("notify_on_error", True))
        self.notify_on_hold = bool(tg.get("notify_on_hold", False))

    def _get_bot_token(self) -> Optional[str]:
        env = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
        if env:
            return env
        try:
            for gateway_config in (
                Path.home() / "AppData" / "Local" / "hermes" / "gateway.yaml",
                Path.home() / ".hermes" / "gateway.yaml",
                Path(os.path.expanduser("~/.hermes/gateway.yaml")),
            ):
                if gateway_config.exists():
                    try:
                        import yaml  # type: ignore
                    except Exception:
                        yaml = None
                    if yaml is None:
                        continue
                    with open(gateway_config, "r", encoding="utf-8") as f:
                        gw_config = yaml.safe_load(f) or {}
                    tok = (gw_config.get("telegram") or {}).get("token")
                    if tok:
                        return tok
        except Exception as e:
            print(f"Warning: Could not load Telegram token: {e}")
        return None

    def _footer(self) -> str:
        return (
            f"\n\n📊 Dashboard: {self.dashboard_url}"
            f"\n⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )

    def send_message(self, message: str, parse_mode: str = "Markdown") -> bool:
        if not self.enabled or not self.bot_token or not self.chat_id:
            print(f"[Telegram Disabled] {message}")
            return False
        if requests is None:
            print("[Telegram] requests not installed")
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "disable_web_page_preview": False,
            }
            response = requests.post(url, json=data, timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"Telegram send error: {e}")
            return False

    def notify_trade_signal(
        self,
        action: str,
        symbol: str,
        price: float,
        qty: float,
        stop: float,
        target: float,
        confidence: int,
        thesis: str,
        dry_run: bool = True,
    ):
        """Notify about a BUY/SELL only."""
        if not self.notify_on_trade:
            return
        action_u = str(action or "").upper()
        if action_u not in ("BUY", "SELL"):
            # Never notify HOLD via trade channel
            if action_u == "HOLD" and not self.notify_on_hold:
                return
            if action_u == "HOLD":
                return
            return

        mode_emoji = "🧪" if dry_run else "💰"
        action_emoji = "🟢" if action_u == "BUY" else "🔴"
        try:
            qty_f = float(qty or 0)
            price_f = float(price or 0)
            stop_f = float(stop or 0)
            target_f = float(target or 0)
        except Exception:
            qty_f, price_f, stop_f, target_f = 0.0, 0.0, 0.0, 0.0

        risk = abs(price_f - stop_f) * qty_f
        reward = abs(target_f - price_f) * qty_f
        rr_ratio = reward / risk if risk > 0 else 0

        message = f"""
{mode_emoji} *PAPER {action_u}* {action_emoji}

*Symbol:* `{symbol}`
*Price:* ${price_f:.2f}
*Qty:* {qty_f:.4f}
*Notional:* ${price_f * qty_f:.2f}

📊 *Risk*
• Stop: ${stop_f:.2f}
• Target: ${target_f:.2f}
• R:R {rr_ratio:.2f}
• Confidence: {confidence}%

💡 {thesis or '—'}
""".strip()
        message += self._footer()
        self.send_message(message)

    def notify_disagreement(self, model1_decision: Dict, model2_decision: Dict, reason: str):
        """Optional. Default off. Never spam pure HOLD/HOLD focus mismatches."""
        if not self.notify_on_disagreement:
            return
        a1 = str((model1_decision or {}).get("action") or "").upper()
        a2 = str((model2_decision or {}).get("action") or "").upper()
        if a1 == "HOLD" and a2 == "HOLD":
            return

        message = f"""
⚠️ *MODEL DISAGREEMENT — NO TRADE*

*Model 1:* {a1} {(model1_decision or {}).get('symbol', 'N/A')} ({(model1_decision or {}).get('confidence', 0)}%)
*Model 2:* {a2} {(model2_decision or {}).get('symbol', 'N/A')} ({(model2_decision or {}).get('confidence', 0)}%)

🚫 {reason}
""".strip()
        message += self._footer()
        self.send_message(message)

    def notify_blocker(self, blocker_type: str, details: str):
        if not self.notify_on_blocker:
            return
        # skip hold-ish noise
        d = (details or "").lower()
        if "hold" in d and "buy" not in d and "sell" not in d:
            return
        message = f"""
🛑 *TRADE BLOCKED — {str(blocker_type).upper()}*

{details}
""".strip()
        message += self._footer()
        self.send_message(message)

    def notify_error(self, error_type: str, error_msg: str, stack_trace: Optional[str] = None):
        if not self.notify_on_error:
            return
        message = f"""
❌ *SYSTEM ERROR — {str(error_type).upper()}*

{error_msg}
""".strip()
        if stack_trace:
            message += f"\n```\n{stack_trace[:500]}\n```"
        message += self._footer()
        self.send_message(message)

    def notify_fill_summary(
        self,
        action: str,
        symbol: str,
        qty: float,
        price: float,
        equity: Optional[float] = None,
        open_pnl: Optional[float] = None,
        thesis: str = "",
        holdings_lines: Optional[list] = None,
    ):
        """Compact BUY/SELL fill for cron/session delivery."""
        if str(action).upper() not in ("BUY", "SELL"):
            return
        if not self.notify_on_trade:
            return
        action_u = str(action).upper()
        emoji = "🟢" if action_u == "BUY" else "🔴"
        lines = [
            f"{emoji} *PAPER FILL — {action_u} {symbol}*",
            f"Qty {float(qty):.4f} @ ${float(price):.2f} (${float(qty)*float(price):.2f})",
        ]
        if equity is not None:
            lines.append(f"Equity ${float(equity):.2f}" + (f" | open P/L ${float(open_pnl):+.2f}" if open_pnl is not None else ""))
        if thesis:
            lines.append(f"💡 {thesis[:180]}")
        if holdings_lines:
            lines.append("Holdings:")
            lines.extend(holdings_lines[:8])
        msg = "\n".join(lines) + self._footer()
        self.send_message(msg)


if __name__ == "__main__":
    notifier = TelegramNotifier()
    print("enabled", notifier.enabled, "chat", bool(notifier.chat_id), "token", bool(notifier.bot_token))
    print("dashboard", notifier.dashboard_url)
