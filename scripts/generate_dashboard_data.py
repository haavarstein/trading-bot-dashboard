#!/usr/bin/env python3
"""Generate public dashboard data from local trading-bot artifacts."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
OUT = ROOT / 'dashboard-data.json'


def read_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def parse_ts(s: str):
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None


def main():
    now = datetime.now(timezone.utc)
    today = now.date()

    candidates_blob = {}
    if (DATA / 'candidates.json').exists():
        candidates_blob = json.loads((DATA / 'candidates.json').read_text(encoding='utf-8'))
    candidates = candidates_blob.get('candidates', [])
    consensus = read_jsonl(DATA / 'consensus_log.jsonl')
    orders = read_jsonl(DATA / 'order_ledger.jsonl')

    today_consensus = [r for r in consensus if (parse_ts(r.get('timestamp', '')) and parse_ts(r.get('timestamp', '')).date() == today)]
    today_orders = [r for r in orders if (parse_ts(r.get('timestamp', '')) and parse_ts(r.get('timestamp', '')).date() == today)]

    valid_count = sum(1 for r in today_consensus if r.get('validation', {}).get('valid') is True)
    blocked_count = sum(1 for r in today_consensus if r.get('validation', {}).get('valid') is False)
    consensus_count = sum(1 for r in today_consensus if r.get('consensus') is True)

    latest_candidate = candidates[0] if candidates else {}
    latest_consensus = today_consensus[-1] if today_consensus else (consensus[-1] if consensus else {})
    latest_order = today_orders[-1] if today_orders else (orders[-1] if orders else {})

    symbols = [r.get('model1', {}).get('symbol') for r in today_consensus if r.get('model1', {}).get('symbol')]
    symbol_counter = Counter(symbols)

    activity = []
    for row in today_consensus[-8:]:
        sym = row.get('model1', {}).get('symbol', 'N/A')
        validation = row.get('validation', {})
        valid = validation.get('valid')
        reason = validation.get('reason')
        status = 'validated' if valid else 'blocked'
        activity.append({
            'type': 'consensus',
            'symbol': sym,
            'status': status,
            'headline': f"Consensus {status.upper()} for {sym}",
            'detail': reason or row.get('model1', {}).get('thesis', ''),
            'timestamp': row.get('timestamp'),
        })
    for row in today_orders[-8:]:
        activity.append({
            'type': 'order',
            'symbol': row.get('symbol', 'N/A'),
            'status': row.get('status', 'SIMULATED').lower(),
            'headline': f"{row.get('action', 'BUY')} {row.get('symbol', 'N/A')} simulated",
            'detail': row.get('thesis', ''),
            'timestamp': row.get('timestamp'),
        })
    activity.sort(key=lambda x: x.get('timestamp') or '', reverse=True)
    activity = activity[:10]

    payload = {
        'generated_at': now.isoformat(),
        'scanner': candidates_blob.get('scanner', 'unknown'),
        'stats': {
            'candidate_count': len(candidates),
            'consensus_count_today': consensus_count,
            'validated_count_today': valid_count,
            'blocked_count_today': blocked_count,
            'simulated_orders_today': len(today_orders),
            'top_symbol_today': symbol_counter.most_common(1)[0][0] if symbol_counter else None,
        },
        'latest_candidate': latest_candidate,
        'latest_consensus': latest_consensus,
        'latest_order': latest_order,
        'activity': activity,
        'top_candidates': candidates[:5],
    }

    OUT.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(str(OUT))


if __name__ == '__main__':
    main()
