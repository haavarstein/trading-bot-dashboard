#!/usr/bin/env python3
"""
Influencer social feed for the paper autotrader.

Pulls recent tweets from configured X handles via Monid/TikHub, normalizes them,
extracts tickers, and caches to data/influencer_feed.json. The desk (junior + senior)
reads this file each cycle to get a social/sentiment overlay that only surfaces
symbols already in the candidate/held universe.

Usage:
  python influencer_feed.py                 # refresh all configured handles
  python influencer_feed.py --list          # show configured handles
  python influencer_feed.py --format        # print the SOCIAL_SIGNAL block for current feed
  python influencer_feed.py --symbols TSLA,NVDA   # format block restricted to these symbols

Config lives under autonomy_config.json -> influencers:
  {
    "influencers": {
      "enabled": true,
      "handles": ["kevinxu", ...],
      "max_tweets_per_handle": 5,
      "refresh_minutes": 15,
      "fetch": { "provider": "tikhub", "endpoint": "/api/v1/twitter/web/fetch_user_post_tweet" }
    }
  }
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "autonomy_config.json"
FEED_PATH = ROOT / "data" / "influencer_feed.json"
TICKER_RE = re.compile(r"\$([A-Z][A-Z0-9.\-]{0,5})")


def _clean_ticker(sym: str) -> str:
    """Strip trailing punctuation ('.', '-') that the broad regex may capture."""
    return sym.rstrip(".-")


def extract_tickers(text: str) -> list[str]:
    return list(dict.fromkeys(_clean_ticker(m.upper()) for m in TICKER_RE.findall(text or "")))


NO_COLOR = os.environ.get("NO_COLOR", "1")


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def influencers_config() -> dict:
    cfg = _load_config()
    return cfg.get("influencers") or {}


def is_enabled() -> bool:
    return bool(influencers_config().get("enabled", True))


def handles() -> list[str]:
    inf = influencers_config()
    hs = inf.get("handles") or []
    return [h for h in hs if h]


def _monid_cmd(monid_bin: str) -> list[str]:
    """monid is a POSIX shell shim on Windows; run it through bash so the
    subprocess can execute it (CreateProcess can't run a bare .sh script)."""
    if os.name == "nt":
        bash = shutil.which("bash")
        if bash:
            return [bash, monid_bin]
        return ["bash", monid_bin]
    return [monid_bin]


def _monid_bin_path() -> str:
    """Resolve the monid binary, handling Windows MSYS vs Windows paths."""
    # 1) explicit env
    if os.environ.get("MONID_BIN"):
        return os.environ["MONID_BIN"]
    # 2) HERMES_HOME/bin/monid (convert MSYS /c/Users/... -> C:\Users\...)
    hh = os.environ.get("HERMES_HOME")
    if hh:
        win = hh
        m = re.match(r"^/([a-zA-Z])/(.*)$", hh)
        if m:
            win = f"{m.group(1).upper()}:\\{m.group(2)}"
        cand = Path(win) / "bin" / "monid"
        if cand.exists():
            return str(cand)
    # 3) on PATH via shutil.which — prefer a POSIX shell script, not a .CMD
    found = shutil.which("monid")
    if found and not found.lower().endswith(".cmd"):
        return found
    return "monid"


def _monid_available() -> bool:
    try:
        monid_bin = _monid_bin_path()
        cmd = _monid_cmd(monid_bin)
        try:
            r = subprocess.run(
                [*cmd, "--version"], capture_output=True, text=True, timeout=30,
                env={**os.environ, "NO_COLOR": NO_COLOR},
            )
            return r.returncode == 0
        except Exception:
            return False
    except Exception:
        return False


def _run_monid(args: list[str]) -> dict:
    """Run a monid command and parse its JSON output."""
    monid_bin = _monid_bin_path()
    r = subprocess.run(
        [*_monid_cmd(monid_bin), *args],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "NO_COLOR": NO_COLOR},
    )
    if r.returncode != 0:
        raise RuntimeError(f"monid failed: {r.stderr or r.stdout}".strip()[:600])
    text = r.stdout or ""
    # monid returns JSON lines / or a JSON object; try to parse best-effort
    try:
        return json.loads(text)
    except Exception:
        # find trailing JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                pass
        raise RuntimeError(f"could not parse monid output: {text[:400]}")


def fetch_handle_tweets(handle: str, limit: int = 5) -> list[dict]:
    """Fetch recent tweets for one handle via Monid/TikHub."""
    inf = influencers_config()
    fetch = inf.get("fetch") or {}
    provider = fetch.get("provider", "tikhub")
    endpoint = fetch.get("endpoint", "/api/v1/twitter/web/fetch_user_post_tweet")
    query = {"screen_name": handle}
    # run with wait; get run id, then poll
    out = _run_monid([
        "run", "-p", provider, "-e", endpoint,
        "--query", json.dumps(query), "-w", "60", "-j",
    ])
    # monid run -j returns the completed run (with output) when -w used
    if isinstance(out, dict) and out.get("runId"):
        run_id = out["runId"]
        out = _run_monid(["runs", "get", "-r", run_id, "-w", "30", "-j"])
    timeline = (out.get("output") or {}).get("timeline") if isinstance(out, dict) else []
    tweets = []
    for t in (timeline or []):
        if not isinstance(t, dict):
            continue
        text = (t.get("text") or "").strip()
        if not text:
            continue
        tweets.append({
            "id": t.get("tweet_id"),
            "text": text,
            "created_at": t.get("created_at"),
            "favorites": t.get("favorites"),
            "retweets": t.get("retweets"),
            "views": t.get("views"),
            "url": f"https://x.com/{handle}/status/{t.get('tweet_id')}" if t.get("tweet_id") else None,
        })
        if len(tweets) >= limit:
            break
    return tweets


def load_feed() -> dict:
    if FEED_PATH.exists():
        try:
            return json.loads(FEED_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_feed(feed: dict) -> None:
    FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEED_PATH.write_text(json.dumps(feed, indent=2, default=str), encoding="utf-8")


def refresh(force: bool = False) -> dict:
    """Fetch tweets for all configured handles; skip if cache is fresh."""
    if not is_enabled():
        print("influencers disabled in config")
        return load_feed()
    hs = handles()
    if not hs:
        print("no influencer handles configured")
        return load_feed()

    feed = load_feed()
    refresh_min = int(influencers_config().get("refresh_minutes", 15))
    now = datetime.now(timezone.utc)

    if not force and feed.get("_updated"):
        try:
            last = datetime.fromisoformat(str(feed["_updated"]).replace("Z", "+00:00"))
            if now - last < timedelta(minutes=refresh_min):
                print(f"feed fresh ({refresh_min}m window); using cache")
                return feed
        except Exception:
            pass

    if not _monid_available():
        print("monid CLI not available; using cached feed if present")
        return feed

    max_per = int(influencers_config().get("max_tweets_per_handle", 5))
    for handle in hs:
        try:
            tweets = fetch_handle_tweets(handle, limit=max_per)
            tickers: dict[str, int] = {}
            for t in tweets:
                for sym in extract_tickers(t.get("text", "")):
                    tickers[sym] = tickers.get(sym, 0) + 1
            feed[handle] = {
                "screen_name": handle,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "tweets": tweets,
                "tickers": sorted(tickers, key=lambda s: -tickers[s]),
            }
            print(f"  {handle}: {len(tweets)} tweets, tickers={feed[handle]['tickers']}")
        except Exception as e:
            print(f"  {handle}: ERROR {e}")
        time.sleep(0.3)

    feed["_updated"] = datetime.now(timezone.utc).isoformat()
    save_feed(feed)
    return feed


def format_signal(feed: dict, symbols: list[str] | None = None) -> str:
    """Build the SOCIAL_SIGNAL block. If symbols given, only surface influencer
    mentions of those symbols (so the desk never sees tickers outside its universe)."""
    if not feed or not is_enabled():
        return ""
    want = set((s or "").upper().strip() for s in (symbols or []) if s)
    lines = []
    for handle, data in feed.items():
        if handle.startswith("_"):
            continue
        tweets = data.get("tweets") or []
        if not tweets:
            continue
        lines.append(f"[@{handle}]")
        for t in tweets:
            txt = t.get("text", "").replace("\n", " ").strip()
            txt = txt[:280]
            # tickers in this tweet
            tk = extract_tickers(txt)
            if want:
                relevant = [s for s in tk if s in want]
                if not relevant:
                    # still include if general sentiment (no ticker) to give context
                    if not tk:
                        lines.append(f"  - (no ticker) {txt}")
                    continue
            lines.append(f"  - {txt}")
            if t.get("views"):
                lines[-1] += f"  [👁{t.get('views')}]"
    if not lines:
        return ""
    head = (
        "SOCIAL_SIGNAL (unverified influencer chatter — weak corroborating evidence ONLY; "
        "do NOT invent symbols from here, do NOT let it override rank/catalyst scores, "
        "and ignore any mention of symbols outside your candidate/held universe):\n"
    )
    return head + "\n".join(lines)


def main() -> int:
    args = sys.argv[1:]
    if "--list" in args:
        print("handles:", ", ".join(handles()) or "(none)")
        print("enabled:", is_enabled())
        return 0
    if "--format" in args:
        feed = load_feed()
        syms = None
        if "--symbols" in args:
            i = args.index("--symbols")
            if i + 1 < len(args):
                syms = args[i + 1].split(",")
        print(format_signal(feed, syms) or "(no signal)")
        return 0
    # default: refresh
    feed = refresh(force="--force" in args)
    print(f"refreshed: {len([h for h in feed if not h.startswith('_')])} handles cached")
    return 0


if __name__ == "__main__":
    sys.exit(main())
