#!/usr/bin/env python3
"""
Post-commit publish notifier.

Sends a Telegram summary when a commit to this repo is a real code/version
publish. Routine cron dashboard-data.json snapshot pushes (the 10-15m mark /
paper-session commits) are suppressed so the chat does not get ~30 pings/day.

Wired as a git `post-commit` hook (.git/hooks/post-commit). Safe to run manually:
    python scripts/notify_publish.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# Dedup state: only notify a given commit once, even if the post-commit hook
# fires more than once (e.g. commit + a manual verification run, or git calling
# the hook for a merge). Stored under the repo's .git so it is local-only.
_DEDUP_PATH = ROOT / ".git" / "notify_publish_last.json"

try:
    from telegram_notifier import TelegramNotifier
except Exception as exc:  # pragma: no cover
    print(f"notify_publish: telegram_notifier import failed: {exc}")
    raise SystemExit(0)


# Files that count as a routine snapshot push (suppressed). Anything else in a
# commit is a real publish and triggers a summary.
ROUTINE_FILES = {"dashboard-data.json"}


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        return r.stdout.strip() or r.stderr.strip()
    except Exception as exc:
        return str(exc)


def _latest_commit() -> dict:
    """Return {hash, subject, files} for the latest commit on the current branch."""
    full = _run(["git", "log", "-1", "--format=%H%x09%s"])
    if not full:
        return {}
    hash_, _, subject = full.partition("\t")
    files = _run(["git", "show", "--name-only", "--format=", hash_]).splitlines()
    files = [f for f in files if f.strip()]
    return {"hash": hash_, "subject": subject, "files": files}


def _is_routine_snapshot(files: list[str]) -> bool:
    if not files:
        return True
    return all(Path(f).as_posix() in ROUTINE_FILES for f in files)


def _load_last_notified() -> str:
    try:
        import json
        if _DEDUP_PATH.exists():
            return str(json.loads(_DEDUP_PATH.read_text(encoding="utf-8")).get("hash", ""))
    except Exception:
        pass
    return ""


def _mark_notified(hash_: str) -> None:
    try:
        import json
        _DEDUP_PATH.write_text(json.dumps({"hash": hash_}), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    commit = _latest_commit()
    if not commit.get("hash"):
        print("notify_publish: no commit found, skipping")
        return 0

    files = commit.get("files") or []
    if _is_routine_snapshot(files):
        # Routine cron dashboard snapshot — no ping.
        return 0

    short = commit["hash"][:7]
    subject = commit.get("subject") or "(no subject)"

    # Dedup: if this exact commit was already notified, do not send again.
    if _load_last_notified() == commit["hash"]:
        print(f"notify_publish: {short} already notified, skipping duplicate")
        return 0

    count = len(files)
    file_list = "\n".join(f"• `{f}`" for f in files[:12])
    if count > 12:
        file_list += f"\n• … +{count - 12} more"

    message = (
        f"🚀 *Trading bot — new publish*\n\n"
        f"*Commit:* `{short}`\n"
        f"*Subject:* {subject}\n"
        f"*Files ({count}):*\n{file_list}\n"
    )

    notifier = TelegramNotifier()
    notifier.send_message(message)
    _mark_notified(commit["hash"])
    print(f"notify_publish: sent publish summary for {short} ({count} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
