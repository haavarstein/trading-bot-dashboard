#!/usr/bin/env python3
"""Shared .env loader.

Loads KEY=VALUE lines from the Hermes config .env(s) and the repo-root .env into
`os.environ` (only keys not already set). Single source of truth — previously the
same loader was copied in dual_llm.py, market_data.py, telegram_notifier.py.
No behavior change.
"""
from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_dotenv() -> None:
    candidates = [
        Path.home() / "AppData" / "Local" / "hermes" / ".env",
        Path.home() / ".hermes" / ".env",
        _repo_root() / ".env",
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
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass
