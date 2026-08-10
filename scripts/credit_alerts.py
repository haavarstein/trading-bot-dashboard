#!/usr/bin/env python3
"""Detect API credit/quota failures and notify Telegram (cooldown-aware)."""

from __future__ import annotations

import re
from typing import Optional

_CREDIT_RE = re.compile(
    r"credit|quota|insufficient|billing|payment required|\b402\b|out of credits|"
    r"exceeded your current quota|rate limit|\b429\b|budget exhausted|"
    r"daily call budget exhausted|too many requests|plan limit|usage limit|spend limit",
    re.I,
)


def looks_like_credit_error(exc_or_text) -> bool:
    return bool(_CREDIT_RE.search(str(exc_or_text or "")))


def http_status_from_exc(exc) -> Optional[int]:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    # urllib HTTPError
    try:
        import urllib.error
        if isinstance(exc, urllib.error.HTTPError):
            return int(exc.code)
    except Exception:
        pass
    m = re.search(r"\b(402|429|403)\b", str(exc or ""))
    if m:
        return int(m.group(1))
    return None


def notify_credit_issue(provider: str, detail: str, http_status: int | None = None) -> bool:
    if not looks_like_credit_error(detail) and http_status not in (402, 429):
        # still allow explicit 403 payment-ish only if text matches
        if http_status != 403 or not looks_like_credit_error(detail):
            return False
    try:
        from telegram_notifier import TelegramNotifier
        n = TelegramNotifier()
        return n.notify_api_credits(provider, detail, http_status=http_status)
    except Exception as exc:
        print(f"[credit_alerts] notify failed: {exc}")
        return False
