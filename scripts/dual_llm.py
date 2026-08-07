#!/usr/bin/env python3
"""
Dual-model decision client for paper autotrader.

Attempts live model calls when API keys are available:
  - model_1 (grok-*): xAI API (XAI_API_KEY) OpenAI-compatible chat completions
  - model_2 (claude-*): Anthropic Messages API (ANTHROPIC_API_KEY)

Falls back to caller-supplied deterministic decision when a provider is missing
or a call fails. Every response is tagged with source=live|fallback.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional


def _load_dotenv_files() -> None:
    candidates = [
        Path.home() / "AppData" / "Local" / "hermes" / ".env",
        Path.home() / ".hermes" / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


_load_dotenv_files()


def _http_json(url: str, headers: dict, payload: dict, timeout: int = 45) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _extract_json_obj(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model text")
    # fenced json
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    if m:
        return json.loads(m.group(1))
    # raw object
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no json object in model response")


def _portfolio_brief(broker: dict) -> str:
    pos = broker.get("positions") or []
    lines = [
        f"cash=${float(broker.get('cash') or 0):.2f}",
        f"equity=${float(broker.get('equity') or broker.get('buying_power') or 0):.2f}",
        f"open_positions={len(pos)}",
    ]
    for p in pos[:8]:
        lines.append(
            f"- {p.get('symbol')}: qty={p.get('qty')} avg={p.get('avg_cost')} "
            f"last={p.get('current_price') or p.get('last')} "
            f"stop={p.get('stop_loss')} target={p.get('take_profit')} "
            f"open_pnl={p.get('unrealized_pnl') or p.get('open_pnl')}"
        )
    return "\n".join(lines)


def _candidates_brief(market: dict, limit: int = 8) -> str:
    rows = []
    for sym, payload in market.items():
        rows.append(
            {
                "symbol": sym,
                "price": payload.get("price"),
                "rank_score": payload.get("rank_score"),
                "catalyst_score": payload.get("catalyst_score"),
                "sentiment": payload.get("sentiment"),
                "catalyst": (payload.get("catalyst") or "")[:180],
                "rsi": payload.get("rsi"),
                "name": payload.get("name"),
            }
        )
    rows.sort(key=lambda r: float(r.get("rank_score") or 0), reverse=True)
    return json.dumps(rows[:limit], indent=2)


def build_prompt(model_name: str, broker: dict, market: dict, rules: dict) -> str:
    max_pos = rules.get("max_position_usd", 200)
    min_rr = rules.get("min_rr", 1.5)
    max_names = rules.get("max_positions", 5)
    return f"""You are desk model `{model_name}` on a simulated US equities paper book.
Return ONLY a single JSON object (no markdown) with keys:
action (BUY|SELL|HOLD), symbol, confidence (0-100 integer), entry_price, stop_loss, take_profit,
qty (float shares), thesis (2-4 sentences), reason_code (new_entry|stop_loss|take_profit|rotation|hold),
bullets (array of 2-4 short strings).

Hard rules:
- Prefer liquid catalyst setups from the candidate list.
- Max new buy notional about ${max_pos}.
- Require stop and target with reward:risk >= {min_rr} on BUY/SELL plans.
- Max open names about {max_names}.
- Book-full policy: you MAY SELL a currently held name to rotate into a clearly better candidate
  (reason_code=rotation). Prefer the weakest hold (worst open P/L or weakest thesis) as SELL symbol.
  Rotation is allowed by desk policy when a new candidate is materially better.
- SELL only a symbol currently held (stop_loss / take_profit / rotation).
- If no clean edge to buy or rotate, action=HOLD. On HOLD set symbol to "CASH" (not a random watch name).
- Do not invent symbols outside candidates/held names.
- Confidence: use >=70 only when recommending BUY/SELL. HOLD may use 55-75.

PORTFOLIO:
{_portfolio_brief(broker)}

CANDIDATES (ranked evidence):
{_candidates_brief(market, int(rules.get('max_candidates_to_llm') or 8))}
"""


def call_xai_grok(model: str, prompt: str, effort: str | None = None) -> dict:
    key = os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY/GROK_API_KEY not set")
    # xAI OpenAI-compatible
    model_id = model
    if model_id in ("grok-4.5", "grok-4.5-latest"):
        # common public id; fall back handled by caller if 404
        model_id = "grok-3"  # safer default id if 4.5 alias unavailable
        # try preferred first via override
        preferred = os.environ.get("XAI_MODEL_ID") or "grok-4-latest"
        model_id = preferred
    payload = {
        "model": model_id,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You are a careful equities trading desk model. Output JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    # optional effort hint for reasoning models
    if effort:
        payload["messages"][0]["content"] += f" Effort setting: {effort}."
    data = _http_json(
        "https://api.x.ai/v1/chat/completions",
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        payload,
    )
    text = data["choices"][0]["message"]["content"]
    obj = _extract_json_obj(text)
    obj["_provider"] = "xai"
    obj["_model_id"] = model_id
    return obj


def call_anthropic_claude(model: str, prompt: str) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    # map friendly names to API ids
    model_id = model
    aliases = {
        "claude-sonnet-5": "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
        "claude-sonnet-4.5": "claude-sonnet-4-5-20250929",
    }
    model_id = os.environ.get("ANTHROPIC_MODEL_ID") or aliases.get(model, model)
    payload = {
        "model": model_id,
        "max_tokens": 900,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = _http_json(
        "https://api.anthropic.com/v1/messages",
        {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        payload,
    )
    parts = data.get("content") or []
    text = ""
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            text += p.get("text") or ""
    obj = _extract_json_obj(text)
    obj["_provider"] = "anthropic"
    obj["_model_id"] = model_id
    return obj


def normalize_decision(raw: dict, model_name: str, source: str) -> dict:
    action = str(raw.get("action") or "HOLD").upper().strip()
    if action not in ("BUY", "SELL", "HOLD"):
        action = "HOLD"
    symbol = str(raw.get("symbol") or "CASH").upper().strip()
    try:
        confidence = int(float(raw.get("confidence") or 70))
    except Exception:
        confidence = 70
    confidence = max(0, min(100, confidence))

    def fnum(key, default=0.0):
        try:
            return float(raw.get(key) if raw.get(key) is not None else default)
        except Exception:
            return float(default)

    thesis = str(raw.get("thesis") or "").strip() or f"{action} decision from {model_name}"
    bullets = raw.get("bullets") if isinstance(raw.get("bullets"), list) else []
    bullets = [str(b) for b in bullets[:6]]
    reason_code = str(raw.get("reason_code") or ("hold" if action == "HOLD" else "new_entry"))
    entry = fnum("entry_price", fnum("price", 0.0))
    stop = fnum("stop_loss", entry * 0.975 if entry else 0.0)
    target = fnum("take_profit", entry * 1.05 if entry else 0.0)
    qty = fnum("qty", 0.0)
    risk = max(abs(entry - stop), 1e-9)
    rr = round(max(abs(target - entry), 0.0) / risk, 2) if entry else 0.0
    reasoning = {
        "headline": f"{'Bought' if action == 'BUY' else 'Sold' if action == 'SELL' else 'Hold'} ${symbol}",
        "narrative": thesis,
        "bullets": bullets
        or [
            f"Source: {source}",
            f"Model: {model_name}",
        ],
        "risk_map": {
            "stop": round(stop, 2),
            "target": round(target, 2),
            "horizon_days": 5,
            "rr": rr,
            "confidence_10": max(1, min(10, int(round(confidence / 10)))),
            "confidence": confidence,
        },
        "thesis": thesis,
    }
    return {
        "model": model_name,
        "action": action,
        "symbol": symbol,
        "confidence": confidence,
        "entry_price": entry,
        "stop_loss": round(stop, 2),
        "take_profit": round(target, 2),
        "qty": qty,
        "thesis": thesis,
        "reason_code": reason_code,
        "reasoning": reasoning,
        "source": source,
        "provider": raw.get("_provider"),
        "provider_model_id": raw.get("_model_id"),
        "bullets": bullets,
    }


def get_live_or_fallback(
    model_name: str,
    broker: dict,
    market: dict,
    rules: dict,
    fallback_fn: Callable[[str, dict, dict], dict],
    effort: str | None = None,
) -> dict:
    """
    Try live provider for model_name; on any failure use fallback_fn(model_name, broker, market).
    """
    prompt = build_prompt(model_name, broker, market, rules)
    name = (model_name or "").lower()
    try:
        if "grok" in name or name.startswith("xai"):
            raw = call_xai_grok(model_name, prompt, effort=effort)
            out = normalize_decision(raw, model_name, source="live")
            return out
        if "claude" in name or "anthropic" in name or "sonnet" in name:
            raw = call_anthropic_claude(model_name, prompt)
            out = normalize_decision(raw, model_name, source="live")
            return out
        # unknown model family
        raise RuntimeError(f"no live provider mapping for {model_name}")
    except Exception as exc:
        fb = fallback_fn(model_name, broker, market)
        fb = dict(fb)
        fb["source"] = "fallback"
        fb["fallback_reason"] = str(exc)[:240]
        # ensure reasoning exists
        if not fb.get("reasoning"):
            fb["reasoning"] = {
                "headline": f"{fb.get('action')} ${fb.get('symbol')}",
                "narrative": fb.get("thesis") or "",
                "bullets": [f"Fallback decision ({exc.__class__.__name__})"],
                "risk_map": {
                    "stop": fb.get("stop_loss"),
                    "target": fb.get("take_profit"),
                    "horizon_days": 5,
                    "rr": 0,
                    "confidence_10": max(1, min(10, int(round(int(fb.get('confidence') or 70) / 10)))),
                    "confidence": fb.get("confidence") or 70,
                },
                "thesis": fb.get("thesis") or "",
            }
        return fb


def provider_status() -> dict:
    return {
        "xai_key": bool(os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")),
        "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
    }
