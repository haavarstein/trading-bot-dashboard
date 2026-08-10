#!/usr/bin/env python3
"""
Simulated paper autotrader.

Uses local PaperBroker as account truth (buy/sell/cash/positions/P&L).
No IBKR MCP required until personal paper account is active.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))

from paper_broker import PaperBroker, load_broker, quote  # noqa: E402
import dual_llm  # noqa: E402

try:
    from telegram_notifier import TelegramNotifier
except ImportError:
    print("Warning: telegram_notifier not found, notifications disabled")
    TelegramNotifier = None


class DryRunAutoTrader:
    def __init__(self, config_path: str = "./config/autonomy_config.json"):
        self.root = Path(__file__).resolve().parent.parent
        self.config_path = str((self.root / config_path).resolve()) if not Path(config_path).is_absolute() else config_path
        # allow relative from cwd
        if not Path(self.config_path).exists():
            self.config_path = config_path
        self.config = self._load_config(self.config_path)
        self.mode = self.config["mode"]
        self.enabled = self.config["enabled"]
        self.telegram = TelegramNotifier(self.config_path) if TelegramNotifier else None

        self.trade_journal_path = self._resolve_data(self.config["data_files"]["trade_journal"])
        self.order_ledger_path = self._resolve_data(self.config["data_files"]["order_ledger"])
        self.consensus_log_path = self._resolve_data(self.config["data_files"]["consensus_log"])
        self._init_data_files()

        starting = float(self.config.get("account", {}).get("starting_capital", 1000))
        portfolio_path = Path(self.trade_journal_path).parent / "portfolio.json"
        self.broker = PaperBroker(starting_cash=starting, path=portfolio_path)

    def _resolve_data(self, path: str) -> str:
        p = Path(path)
        if not p.is_absolute():
            p = self.root / path
        return str(p)

    def _load_config(self, config_path: str) -> Dict:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert config["mode"] in ["DRY_RUN", "PAPER_TRADING", "LIVE"], "Mode must be DRY_RUN, PAPER_TRADING, or LIVE"
        assert config["enabled"] is False or config["mode"] in ["PAPER_TRADING", "LIVE"], "Cannot enable DRY_RUN mode"
        return config

    def _init_data_files(self):
        for path in [self.trade_journal_path, self.order_ledger_path, self.consensus_log_path]:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                open(path, "w", encoding="utf-8").close()

    def check_kill_switch(self) -> bool:
        kill_switch_path = self.config["kill_switch"]["file_path"]
        if not Path(kill_switch_path).is_absolute():
            kill_switch_path = str(self.root / kill_switch_path)
        if os.path.exists(kill_switch_path):
            print(f"🔴 KILL SWITCH ACTIVE: {kill_switch_path}")
            return True
        return False

    def log_to_ledger(self, ledger_path: str, entry: Dict):
        entry = dict(entry)
        entry["timestamp"] = entry.get("timestamp") or datetime.now(timezone.utc).isoformat()
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def get_broker_snapshot(self) -> Dict:
        snap = self.broker.snapshot()
        # Compatibility keys used by older validators
        snap["buying_power"] = snap["cash"]
        return snap

    def get_market_data(self, symbol: str, candidate: Optional[Dict] = None) -> Dict:
        price = quote(symbol)
        base = {
            "symbol": symbol,
            "price": float(price) if price is not None else 0.0,
            "volume": 0,
            "rsi": 50.0,
            "ema50": float(price) if price is not None else 0.0,
        }
        if candidate:
            base.update(candidate)
            if not base.get("price") and candidate.get("price"):
                base["price"] = float(candidate["price"])
        return base

    def _position_map(self, broker_snapshot: Dict) -> Dict[str, Dict]:
        return {p["symbol"]: p for p in broker_snapshot.get("positions", [])}


    def _risk_map(self, entry: float, stop: float, target: float, confidence: int, horizon_days: int = 5) -> Dict:
        risk = max(abs(entry - stop), 1e-9)
        reward = max(abs(target - entry), 0.0)
        rr = round(reward / risk, 2)
        return {
            "stop": round(float(stop), 2),
            "target": round(float(target), 2),
            "horizon_days": horizon_days,
            "rr": rr,
            "confidence_10": max(1, min(10, int(round(confidence / 10)))),
            "confidence": int(confidence),
        }

    def _build_buy_reasoning(self, symbol: str, payload: Dict, entry: float, stop: float, target: float, qty: float, confidence: int, score: float) -> Dict:
        name = payload.get("name") or symbol
        catalyst = str(payload.get("catalyst") or "ranked catalyst setup")
        sentiment = str(payload.get("sentiment") or "mixed")
        headlines = payload.get("top_headlines") or []
        rank = payload.get("rank_score")
        cat = payload.get("catalyst_score")
        notional = round(qty * entry, 2)
        narrative = (
            f"{name} ({symbol}) screened as a {sentiment} catalyst setup on Alpha Radar. "
            f"Primary driver: {catalyst}. "
            f"Entry sized at ${notional:.2f} under the ${float(self.config['position_limits']['max_position_size_usd']):.0f} single-name cap "
            f"with predefined invalidation and upside map before the next 15-minute loop."
        )
        bullets = [
            f"Rank/catalyst scores: rank={rank}, catalyst={cat}; sentiment={sentiment}.",
            f"Measured starter ${notional:.2f} with stop under structure and target into the extension zone.",
        ]
        if headlines:
            bullets.append(f"Lead headline: {headlines[0]}")
        if len(headlines) > 1:
            bullets.append(f"Secondary confirmation: {headlines[1]}")
        risk = self._risk_map(entry, stop, target, confidence)
        return {
            "headline": f"Bought ${symbol}",
            "narrative": narrative,
            "bullets": bullets,
            "risk_map": risk,
            "thesis": narrative,
        }

    def _build_sell_reasoning(self, symbol: str, pos: Dict, px: float, reason_code: str, thesis: str, confidence: int) -> Dict:
        stop = float(pos.get("stop_loss") or px * 0.97)
        target = float(pos.get("take_profit") or px * 1.03)
        avg = float(pos.get("avg_cost") or px)
        pnl = round((px - avg) * float(pos.get("qty") or 0), 2)
        if reason_code == "stop_loss":
            narrative = (
                f"HERMES AUTO-TRADE: Exiting full {symbol} position on stop-loss. "
                f"Price ${px:.2f} tagged invalidation at ${stop:.2f}. Realized path ${pnl:+.2f}."
            )
            bullets = [
                "Hard stop hit; capital preservation over thesis hope.",
                "Position closed in full; no scale-out remaining.",
            ]
        elif reason_code == "take_profit":
            narrative = (
                f"HERMES AUTO-TRADE: Exiting full {symbol} position on take-profit. "
                f"Price ${px:.2f} reached target ${target:.2f}. Realized path ${pnl:+.2f}."
            )
            bullets = [
                "Target zone filled; lock gains and free risk budget.",
                "No trail extension in current paper rules.",
            ]
        elif reason_code == "rotation":
            narrative = (
                f"HERMES AUTO-TRADE: Exiting full {symbol} position via gated rotation. "
                f"{thesis} Mark ${px:.2f} vs avg ${avg:.2f} (open P/L path ${pnl:+.2f})."
            )
            bullets = [
                "Rotation only after min-hold gate and stronger ranked replacement.",
                "Original stop/target were not the primary exit trigger on this fill.",
                "Capital recycled toward higher-scoring catalyst setup.",
            ]
        else:
            narrative = thesis or f"Exiting {symbol} at ${px:.2f}."
            bullets = [thesis or "Discretionary/paper exit."]
        risk = self._risk_map(avg, stop, target, confidence)
        return {
            "headline": f"Sold ${symbol}",
            "narrative": narrative,
            "bullets": bullets,
            "risk_map": risk,
            "thesis": narrative,
        }


    def get_model_decision(self, model_name: str, broker_snapshot: Dict, market_data: Dict) -> Dict:
        """Deterministic paper decision: exits first, then ranked buys, else HOLD."""
        if not market_data:
            raise RuntimeError("No market data provided to decision engine")

        positions = self._position_map(broker_snapshot)

        # 1) Forced exits on stop / target
        for sym, pos in positions.items():
            px = float(pos.get("current_price") or 0)
            stop = pos.get("stop_loss")
            target = pos.get("take_profit")
            if stop is not None and px and px <= float(stop):
                reason = self._build_sell_reasoning(sym, pos, px, "stop_loss", f"Stop-loss exit for {sym}", 95)
                return {
                    "model": model_name,
                    "action": "SELL",
                    "symbol": sym,
                    "confidence": 95,
                    "entry_price": px,
                    "stop_loss": float(stop),
                    "take_profit": float(target or px),
                    "qty": float(pos["qty"]),
                    "thesis": reason["thesis"],
                    "reason_code": "stop_loss",
                    "reasoning": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            if target is not None and px and px >= float(target):
                reason = self._build_sell_reasoning(sym, pos, px, "take_profit", f"Take-profit exit for {sym}", 90)
                return {
                    "model": model_name,
                    "action": "SELL",
                    "symbol": sym,
                    "confidence": 90,
                    "entry_price": px,
                    "stop_loss": float(stop or px),
                    "take_profit": float(target),
                    "qty": float(pos["qty"]),
                    "thesis": reason["thesis"],
                    "reason_code": "take_profit",
                    "reasoning": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

        # 2) Rank candidates for new buys
        ranked = []
        for symbol, payload in market_data.items():
            price = float(payload.get("price", 0) or 0)
            if price <= 0:
                continue
            rank_score = float(payload.get("rank_score", 0) or 0)
            catalyst_score = float(payload.get("catalyst_score", 0) or 0)
            rsi = float(payload.get("rsi", 50) or 50)
            ema50 = float(payload.get("ema50", price) or price)
            sentiment = str(payload.get("sentiment", "mixed"))
            sentiment_bonus = 6 if sentiment == "bullish" else (2 if sentiment == "mixed" else -4)
            trend_bonus = 4 if price >= ema50 else -3
            rsi_bonus = 4 if 45 <= rsi <= 65 else (1 if 35 <= rsi < 45 else -2)
            # Prefer names not already held hard
            held_penalty = -25 if symbol in positions else 0
            # Model personality on fallback path so dual desks are not identical clones.
            mn = (model_name or "").lower()
            if "grok" in mn:
                style = 0.35 * catalyst_score + 0.15 * rank_score + (3 if sentiment == "bullish" else 0)
            elif "claude" in mn or "sonnet" in mn:
                style = 0.15 * catalyst_score + 0.30 * rank_score + rsi_bonus * 0.8 + (2 if price >= ema50 else -2)
            else:
                style = 0.0
            score = rank_score + catalyst_score + sentiment_bonus + trend_bonus + rsi_bonus + held_penalty + style
            ranked.append((score, symbol, payload))

        ranked.sort(key=lambda x: x[0], reverse=True)
        max_positions = int(self.config["position_limits"]["max_positions"])
        cash = float(broker_snapshot.get("buying_power") or broker_snapshot.get("cash") or 0)

        # 3) Rotation is DISABLED by default.
        # Previous behavior sold the weakest open name every cycle whenever a new
        # scanner favorite appeared, often at a small loss and *before* stop/target.
        # That caused the 15-minute "sell red" pattern. Exits are stop/target only
        # unless config explicitly enables rotation with hard gates.
        allow_rotation = bool(self.config.get("execution_rules", {}).get("allow_rotation", False))
        if allow_rotation and len(positions) >= max_positions and ranked:
            best_score, best_sym, best_payload = ranked[0]
            min_edge = float(self.config.get("execution_rules", {}).get("rotation_min_score", 80))
            min_hold_minutes = float(self.config.get("execution_rules", {}).get("rotation_min_hold_minutes", 60))
            require_winner = bool(self.config.get("execution_rules", {}).get("rotation_require_open_winner", True))
            if best_sym not in positions and best_score >= min_edge:
                weakest_sym = None
                weakest_metric = None
                for sym, pos in positions.items():
                    metric = float(pos.get("open_pnl_pct") or 0.0)
                    if weakest_metric is None or metric < weakest_metric:
                        weakest_metric = metric
                        weakest_sym = sym
                if weakest_sym is not None:
                    # Optional: never rotate a loser just to chase a new headline name.
                    if require_winner and (weakest_metric is None or weakest_metric < 0):
                        weakest_sym = None
                    # Optional minimum hold time
                    if weakest_sym is not None:
                        opened_at = positions[weakest_sym].get("opened_at")
                        if opened_at:
                            try:
                                opened_dt = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                                age_min = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 60.0
                                if age_min < min_hold_minutes:
                                    weakest_sym = None
                            except Exception:
                                pass
                if weakest_sym:
                    px = float(positions[weakest_sym]["current_price"])
                    thesis = f"Rotate out of {weakest_sym} to free risk budget for stronger setup {best_sym}"
                    reason = self._build_sell_reasoning(weakest_sym, positions[weakest_sym], px, "rotation", thesis, 78)
                    return {
                        "model": model_name,
                        "action": "SELL",
                        "symbol": weakest_sym,
                        "confidence": 78,
                        "entry_price": px,
                        "stop_loss": float(positions[weakest_sym].get("stop_loss") or px * 0.97),
                        "take_profit": float(positions[weakest_sym].get("take_profit") or px * 1.03),
                        "qty": float(positions[weakest_sym]["qty"]),
                        "thesis": reason["thesis"],
                        "reason_code": "rotation",
                        "reasoning": reason,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

        # 4) Buy top candidate if room + cash
        min_pos = float(self.config["position_limits"]["min_position_size_usd"])
        max_pos = float(self.config["position_limits"]["max_position_size_usd"])
        if ranked and len(positions) < max_positions and cash >= min_pos:
            best_score, best_symbol, best_payload = ranked[0]
            # Avoid piling into same name beyond one full unit if already held near max
            if best_symbol in positions:
                held_val = float(positions[best_symbol].get("market_value") or 0)
                if held_val >= max_pos * 0.9:
                    # try next not-held
                    alt = next(((s, p, sc) for sc, s, p in ranked if s not in positions), None)
                    if alt is None:
                        return self._hold_decision(model_name, broker_snapshot, "Already fully allocated in top name")
                    best_symbol, best_payload, best_score = alt[0], alt[1], alt[2]

            entry_price = float(best_payload["price"])
            stop_loss = round(entry_price * 0.975, 2)
            take_profit = round(entry_price * 1.05, 2)
            budget = min(max_pos, cash)
            # leave a little cash buffer
            budget = min(budget, max(0.0, cash - 5.0))
            if budget < min_pos:
                return self._hold_decision(model_name, broker_snapshot, "Not enough cash for minimum position")

            qty = math.floor((budget / entry_price) * 10000) / 10000
            min_qty = math.ceil((min_pos / entry_price) * 10000) / 10000
            qty = max(qty, min_qty)
            # clamp to cash
            while qty > 0 and round(qty * entry_price, 2) > cash + 0.01:
                qty = round(qty - 0.0001, 4)
            if qty <= 0 or round(qty * entry_price, 2) < min_pos:
                return self._hold_decision(model_name, broker_snapshot, "Sized position below minimum")

            confidence = max(
                self.config["consensus_rules"]["min_confidence"],
                min(95, int(70 + min(20, (best_score or 0) / 3))),
            )
            reason = self._build_buy_reasoning(
                best_symbol, best_payload, entry_price, stop_loss, take_profit, qty, confidence, float(best_score or 0)
            )
            return {
                "model": model_name,
                "action": "BUY",
                "symbol": best_symbol,
                "confidence": confidence,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "qty": qty,
                "thesis": reason["thesis"],
                "reason_code": "new_entry",
                "reasoning": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return self._hold_decision(model_name, broker_snapshot, "No actionable edge after portfolio checks")

    def _hold_decision(self, model_name: str, broker_snapshot: Dict, why: str) -> Dict:
        positions = broker_snapshot.get("positions") or []
        symbol = positions[0]["symbol"] if positions else "CASH"
        px = float(positions[0]["current_price"]) if positions else 0.0
        return {
            "model": model_name,
            "action": "HOLD",
            "symbol": symbol,
            "confidence": 80,
            "entry_price": px,
            "stop_loss": px * 0.99 if px else 0.0,
            "take_profit": px * 1.01 if px else 0.0,
            "qty": 0,
            "thesis": why,
            "reason_code": "hold",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def check_consensus(self, decision1: Dict, decision2: Dict) -> Tuple[bool, Optional[str]]:
        a1 = str(decision1.get("action") or "").upper()
        a2 = str(decision2.get("action") or "").upper()
        if a1 != a2:
            return False, f"Action mismatch: {a1} vs {a2}"

        # HOLD = no trade. Symbol is just "what they watched"; do not require match.
        if a1 == "HOLD":
            # Soft confidence floor for patient holds (still want non-junk)
            hold_floor = min(50, int(self.config["consensus_rules"]["min_confidence"]))
            if int(decision1.get("confidence") or 0) < hold_floor:
                return False, f"Model 1 HOLD confidence {decision1.get('confidence')}% < {hold_floor}%"
            if int(decision2.get("confidence") or 0) < hold_floor:
                return False, f"Model 2 HOLD confidence {decision2.get('confidence')}% < {hold_floor}%"
            return True, None

        if decision1.get("symbol") != decision2.get("symbol"):
            return False, f"Symbol mismatch: {decision1.get('symbol')} vs {decision2.get('symbol')}"

        min_confidence = self.config["consensus_rules"]["min_confidence"]
        if decision1["confidence"] < min_confidence:
            return False, f"Model 1 confidence {decision1['confidence']}% < {min_confidence}%"
        if decision2["confidence"] < min_confidence:
            return False, f"Model 2 confidence {decision2['confidence']}% < {min_confidence}%"

        if a1 in ("BUY", "SELL"):
            entry = float(decision1.get("entry_price") or 0) or 0.0
            if entry:
                try:
                    if abs(float(decision1.get("stop_loss") or 0) - float(decision2.get("stop_loss") or 0)) / entry > 0.05:
                        return False, f"Stop loss mismatch: ${decision1.get('stop_loss')} vs ${decision2.get('stop_loss')}"
                    if abs(float(decision1.get("take_profit") or 0) - float(decision2.get("take_profit") or 0)) / entry > 0.05:
                        return False, f"Take profit mismatch: ${decision1.get('take_profit')} vs ${decision2.get('take_profit')}"
                except Exception:
                    pass
        return True, None

    def validate_order(self, decision: Dict, broker_snapshot: Dict) -> Tuple[bool, Optional[str]]:
        if self.check_kill_switch():
            return False, "Kill switch active"

        action = decision["action"]
        if action == "HOLD":
            return True, None

        symbol = decision["symbol"]
        entry = float(decision["entry_price"])
        stop = float(decision.get("stop_loss") or 0)
        target = float(decision.get("take_profit") or 0)
        qty = float(decision.get("qty") or 0)

        allowed = self.config.get("allowed_symbols", "AI_DECIDES")
        if allowed != "AI_DECIDES" and symbol not in allowed:
            return False, f"{symbol} not in allowed symbols"

        if action == "SELL":
            held = {p["symbol"]: p for p in broker_snapshot.get("positions", [])}
            if symbol not in held:
                return False, f"No open position to sell for {symbol}"
            if qty <= 0:
                return False, "Sell qty must be positive"
            return True, None

        if action != "BUY":
            return False, f"Unsupported action {action}"

        position_value = round(entry * qty, 2)
        cash = round(float(broker_snapshot.get("buying_power") or broker_snapshot.get("cash") or 0), 2)
        if position_value - cash > 0.01:
            return False, f"Insufficient buying power: ${cash:.2f} < ${position_value:.2f}"

        max_position = round(float(self.config["position_limits"]["max_position_size_usd"]), 2)
        min_position = round(float(self.config["position_limits"]["min_position_size_usd"]), 2)
        if position_value - max_position > 0.01:
            return False, f"Position size ${position_value:.2f} > max ${max_position:.2f}"
        if min_position - position_value > 0.01:
            return False, f"Position size ${position_value:.2f} < min ${min_position:.2f}"

        max_positions = int(self.config["position_limits"]["max_positions"])
        open_count = len(broker_snapshot.get("positions") or [])
        held_symbols = {p["symbol"] for p in broker_snapshot.get("positions", [])}
        if symbol not in held_symbols and open_count >= max_positions:
            return False, f"Max positions reached ({max_positions})"

        risk = abs(entry - stop) * qty
        reward = abs(target - entry) * qty
        rr_ratio = reward / risk if risk > 0 else 0
        min_rr = self.config["order_limits"]["min_risk_reward_ratio"]
        if rr_ratio < min_rr:
            return False, f"Risk/Reward {rr_ratio:.2f} < minimum {min_rr}"

        if not stop:
            return False, "Stop loss required but not provided"
        if not target:
            return False, "Take profit required but not provided"

        stop_distance_pct = abs(entry - stop) / entry * 100 if entry else 0
        if stop_distance_pct < self.config["risk_rules"]["min_stop_distance_pct"]:
            return False, f"Stop too tight: {stop_distance_pct:.2f}%"
        if stop_distance_pct > self.config["risk_rules"]["max_stop_distance_pct"]:
            return False, f"Stop too wide: {stop_distance_pct:.2f}%"

        return True, None

    def execute_trade(self, decision: Dict, dry_run: bool = True):
        symbol = decision["symbol"]
        action = decision["action"]
        qty = float(decision.get("qty") or 0)
        entry = float(decision.get("entry_price") or 0)
        stop = decision.get("stop_loss")
        target = decision.get("take_profit")

        fill = None
        status = "HOLD"
        if action == "HOLD":
            status = "HOLD"
        elif action == "BUY":
            fill = self.broker.buy(
                symbol,
                qty,
                entry,
                stop_loss=float(stop) if stop is not None else None,
                take_profit=float(target) if target is not None else None,
                thesis=decision.get("thesis", ""),
                confidence=decision.get("confidence"),
            )
            status = "FILLED_PAPER"
        elif action == "SELL":
            fill = self.broker.sell(
                symbol,
                qty=qty,
                price=entry,
                reason=decision.get("reason_code", "signal"),
                thesis=decision.get("thesis", ""),
            )
            status = "FILLED_PAPER"

        order_entry = {
            "mode": "PAPER_SIM",
            "action": action,
            "symbol": symbol,
            "qty": qty,
            "entry_price": entry,
            "stop_loss": stop,
            "take_profit": target,
            "confidence": decision.get("confidence"),
            "thesis": decision.get("thesis"),
            "reason_code": decision.get("reason_code"),
            "reasoning": decision.get("reasoning"),
            "status": status,
            "fill": fill,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.log_to_ledger(self.order_ledger_path, order_entry)

        if self.telegram and action in ("BUY", "SELL"):
            try:
                self.telegram.notify_trade_signal(
                    action=action,
                    symbol=symbol,
                    price=entry,
                    qty=qty,
                    stop=stop or 0,
                    target=target or 0,
                    confidence=decision.get("confidence", 0),
                    thesis=decision.get("thesis", ""),
                    dry_run=True,
                )
            except Exception as exc:
                print(f"Telegram notify failed: {exc}")

        tag = "[PAPER]"
        if action == "HOLD":
            print(f"\n{tag} HOLD — {decision.get('thesis')}")
        else:
            print(f"\n{tag} {action} {qty} {symbol} @ ${entry:.2f} -> {status}")
            if stop and target and action == "BUY":
                print(f"  Stop: ${float(stop):.2f} | Target: ${float(target):.2f}")
            if fill and action == "SELL":
                print(f"  Realized P/L: ${float(fill.get('realized_pnl') or 0):.2f} ({fill.get('reason')})")
            print(f"  Thesis: {decision.get('thesis')}")

        snap = self.broker.snapshot()
        print(
            f"  Portfolio: equity ${snap['equity']:.2f} | cash ${snap['cash']:.2f} | "
            f"open P/L ${snap['open_pnl']:.2f} | realized ${snap['realized_pnl']:.2f}"
        )

    def run_trading_cycle(self):
        print(f"\n{'=' * 60}")
        print(f"Trading Bot Cycle - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"Mode: {self.mode} | Enabled: {self.enabled}")
        print(f"{'=' * 60}\n")

        if self.check_kill_switch():
            return

        broker_snapshot = self.get_broker_snapshot()
        print(
            f"📊 Paper account: equity ${broker_snapshot['equity']:.2f} | "
            f"cash ${broker_snapshot['cash']:.2f} | positions {len(broker_snapshot.get('positions') or [])}"
        )

        model1_name = self.config["consensus_rules"]["model_1"]
        model2_name = self.config["consensus_rules"]["model_2"]
        effort = self.config.get("consensus_rules", {}).get("model_1_effort")
        effort_txt = f" (effort={effort})" if effort else ""
        print(f"🤖 Requesting decisions from {model1_name}{effort_txt} and {model2_name}...")

        allowed = self.config.get("allowed_symbols", "AI_DECIDES")
        market_context: Dict[str, Dict] = {}
        if allowed == "AI_DECIDES":
            candidates_file = self.root / "data" / "candidates.json"
            if not candidates_file.exists():
                raise RuntimeError("Alpha Radar candidates file missing; run alpha_radar.py first")
            radar_data = json.loads(candidates_file.read_text(encoding="utf-8"))
            candidate_records = radar_data.get("candidates", [])
            max_to_llm = int(self.config.get("consensus_rules", {}).get("max_candidates_to_llm") or 0)
            if max_to_llm > 0:
                candidate_records = candidate_records[:max_to_llm]
            for candidate in candidate_records:
                sym = candidate.get("symbol")
                if not sym:
                    continue
                market_context[sym] = self.get_market_data(sym, candidate)
        else:
            for sym in allowed:
                market_context[sym] = self.get_market_data(sym)

        # Ensure held symbols have market data for exits
        for pos in broker_snapshot.get("positions", []):
            sym = pos["symbol"]
            if sym not in market_context:
                market_context[sym] = self.get_market_data(sym)

        if not market_context:
            raise RuntimeError("No candidates available for model review")

        cr = self.config.get("consensus_rules") or {}
        rules = {
            "max_position_usd": self.config.get("position_limits", {}).get("max_position_size_usd", 200),
            "max_positions": self.config.get("position_limits", {}).get("max_positions", 5),
            "min_rr": self.config.get("order_limits", {}).get("min_risk_reward_ratio", 1.5),
            "max_candidates_to_llm": cr.get("max_candidates_to_llm", 8),
            # junior/senior desk config
            "junior_enabled": cr.get("junior_enabled", True),
            "junior_model_1": cr.get("junior_model_1", "grok-4.3"),
            "junior_model_1_fallback": cr.get("junior_model_1_fallback", "grok-build-0.1"),
            "junior_model_2": cr.get("junior_model_2", "claude-haiku-4-5"),
            "model_1": cr.get("model_1", model1_name),
            "model_1_effort": cr.get("model_1_effort", effort),
            "model_2": cr.get("model_2", model2_name),
            "min_confidence": cr.get("min_confidence", 70),
            "junior_hold_min_confidence": cr.get("junior_hold_min_confidence", 55),
            "borderline_confidence_band": cr.get("borderline_confidence_band", 5),
            "escalate_on_buy_sell": cr.get("escalate_on_buy_sell", True),
            "escalate_on_junior_disagree": cr.get("escalate_on_junior_disagree", True),
            "escalate_on_borderline_confidence": cr.get("escalate_on_borderline_confidence", True),
        }
        status = dual_llm.provider_status()
        print(
            f"  providers: xai={'yes' if status.get('xai_key') else 'no'} | "
            f"anthropic={'yes' if status.get('anthropic_key') else 'no'}"
        )
        print(
            f"  desk: juniors {rules['junior_model_1']}+{rules['junior_model_2']} → "
            f"seniors {rules['model_1']}+{rules['model_2']} (escalate BUY/SELL/disagree/borderline)"
        )

        def _fallback(name, broker, market):
            return self.get_model_decision(name, broker, market)

        desk = dual_llm.run_junior_senior_consensus(
            broker_snapshot,
            market_context,
            rules,
            _fallback,
            self.check_consensus,
        )
        decision1 = desk["decision1"]
        decision2 = desk["decision2"]
        decision1.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        decision2.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

        j1, j2 = desk.get("junior1"), desk.get("junior2")
        if j1 and j2:
            print(
                f"  junior {j1.get('model')}: {j1.get('action')} {j1.get('symbol')} @ {j1.get('confidence')}% "
                f"[{j1.get('source','?')}/{j1.get('provider_model_id','')}]"
            )
            print(
                f"  junior {j2.get('model')}: {j2.get('action')} {j2.get('symbol')} @ {j2.get('confidence')}% "
                f"[{j2.get('source','?')}/{j2.get('provider_model_id','')}]"
            )
            print(
                f"  junior_agree={desk.get('junior_agreed')} escalate={desk.get('tier')=='senior_escalated'} "
                f"reason={desk.get('escalate_reason')}"
            )
        if desk.get("tier") == "senior_escalated":
            print(
                f"  senior {decision1.get('model')}: {decision1.get('action')} {decision1.get('symbol')} "
                f"@ {decision1.get('confidence')}% [{decision1.get('source','?')}/{decision1.get('provider_model_id','')}]"
            )
            print(
                f"  senior {decision2.get('model')}: {decision2.get('action')} {decision2.get('symbol')} "
                f"@ {decision2.get('confidence')}% [{decision2.get('source','?')}/{decision2.get('provider_model_id','')}]"
            )
        else:
            print(
                f"  final(junior_only) {decision1.get('action')} {decision1.get('symbol')} / "
                f"{decision2.get('action')} {decision2.get('symbol')}"
            )

        consensus = bool(desk.get("consensus"))
        reason = desk.get("consensus_reason")
        consensus_entry = {
            "model1": decision1,
            "model2": decision2,
            "junior1": j1,
            "junior2": j2,
            "senior1": desk.get("senior1"),
            "senior2": desk.get("senior2"),
            "tier": desk.get("tier"),
            "escalate_reason": desk.get("escalate_reason"),
            "junior_agreed": desk.get("junior_agreed"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "consensus": consensus,
            "reason": reason,
        }

        if not consensus:
            print(f"\n🚫 CONSENSUS BLOCKED: {reason}")
            self.log_to_ledger(self.consensus_log_path, consensus_entry)
            if self.telegram:
                try:
                    self.telegram.notify_disagreement(decision1, decision2, reason)
                except Exception:
                    pass
            return

        print(f"\n✅ CONSENSUS REACHED: {decision1['action']} {decision1['symbol']}")
        valid, blocker_reason = self.validate_order(decision1, broker_snapshot)
        consensus_entry["validation"] = {"valid": valid, "reason": blocker_reason}
        self.log_to_ledger(self.consensus_log_path, consensus_entry)

        if not valid:
            print(f"🛑 VALIDATION BLOCKED: {blocker_reason}")
            if self.telegram:
                try:
                    self.telegram.notify_blocker("VALIDATION_FAILED", blocker_reason)
                except Exception:
                    pass
            return

        print("✅ VALIDATION PASSED")
        self.execute_trade(decision1, dry_run=True)
        print(f"\n{'=' * 60}\n")


if __name__ == "__main__":
    try:
        # Prefer running from repo root
        os.chdir(Path(__file__).resolve().parent.parent)
        trader = DryRunAutoTrader()
        trader.run_trading_cycle()
    except Exception as e:
        import traceback

        print(f"\n❌ ERROR: {e}")
        traceback.print_exc()
        try:
            if TelegramNotifier:
                from credit_alerts import looks_like_credit_error, notify_credit_issue
                if looks_like_credit_error(e):
                    notify_credit_issue("trading-bot", f"AUTOTRADER_CRASH: {e}")
                else:
                    TelegramNotifier().notify_error("AUTOTRADER_CRASH", str(e), traceback.format_exc())
        except Exception:
            pass
