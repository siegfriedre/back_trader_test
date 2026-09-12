"""Read and join audited case events/trades without rerunning a backtest.

Examples:
  python review_events.py --run results/comparisons/RUN_ID --list-cases
  python review_events.py --run results/comparisons/RUN_ID --case long_history__user_rules__tqqq70 --from-date 2008-09-01 --to-date 2008-10-31

Selected log files are checked against the completed run's manifest. This is
integrity checking, not market-data certification. No files or orders are changed.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re
import sys


def strict_json(text: str):
    def invalid(value):
        raise ValueError(f'Non-finite JSON constant: {value}')
    return json.loads(text, parse_constant=invalid)


def load_manifest(root: Path) -> dict:
    manifest = strict_json((root / 'manifest.json').read_text(encoding='utf-8-sig'))
    if manifest.get('status') != 'passed' or not isinstance(manifest.get('output_sha256'), dict):
        raise ValueError('Only completed run manifests with output hashes can be reviewed')
    return manifest


def case_names(manifest: dict) -> list[str]:
    names = []
    for path in manifest['output_sha256']:
        parts = path.split('/')
        if len(parts) == 2 and parts[1] == 'events.jsonl' and re.fullmatch(r'[A-Za-z0-9_]+', parts[0]):
            names.append(parts[0])
    return sorted(names)


def checked_bytes(root: Path, relative: str, manifest: dict) -> bytes:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path escapes run directory')
    expected = manifest['output_sha256'].get(relative)
    if not isinstance(expected, str):
        raise ValueError(f'File absent from manifest: {relative}')
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError(f'Checksum mismatch: {relative}')
    return payload


def collect(root: Path, case: str, *, from_date: str | None = None,
            to_date: str | None = None, event: str | None = None,
            asset: str | None = None) -> tuple[dict, list[dict]]:
    root = Path(root)
    if not re.fullmatch(r'[A-Za-z0-9_]+', case):
        raise ValueError('Invalid case name')
    left = date.fromisoformat(from_date) if from_date else date.min
    right = date.fromisoformat(to_date) if to_date else date.max
    if left > right:
        raise ValueError('Require from-date <= to-date; both dates are inclusive')
    manifest_bytes = (root / 'manifest.json').read_bytes()
    manifest = load_manifest(root)
    if case not in case_names(manifest):
        raise ValueError('Case is absent from completed manifest; use --list-cases')
    config = strict_json(checked_bytes(root, f'{case}/config.json', manifest).decode('utf-8-sig'))
    events_text = checked_bytes(root, f'{case}/events.jsonl', manifest).decode('utf-8-sig')
    trades_text = checked_bytes(root, f'{case}/trades.csv', manifest).decode('utf-8-sig')
    trades = {}
    for trade in csv.DictReader(io.StringIO(trades_text)):
        key = int(trade['execution_id'])
        trades.setdefault(key, []).append(trade)
    seen, selected = set(), []
    for line_number, line in enumerate(events_text.splitlines(), 1):
        if not line.strip():
            continue
        record = strict_json(line)
        if not isinstance(record, dict):
            raise ValueError(f'Expected event object at line {line_number}')
        key = record.get('event_id')
        if type(key) is not int or key < 1 or key in seen:
            raise ValueError(f'Invalid/duplicate event ID at line {line_number}')
        seen.add(key)
        day = date.fromisoformat(record['execution_date'])
        linked = trades.get(key, [])
        if not left <= day <= right:
            continue
        if event and event.upper() not in str(record.get('event', '')).upper():
            continue
        if asset and not (str(record.get('asset', '')).upper() == asset.upper() or
                          any(str(t['asset']).upper() == asset.upper() for t in linked)):
            continue
        selected.append({**record, 'trades': linked})
    if set(trades) - seen:
        raise ValueError('A trade references an event ID absent from the event log')
    if manifest_bytes != (root / 'manifest.json').read_bytes():
        raise ValueError('Manifest changed during review')
    return config, selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, required=True, help='Completed result directory, not its parent')
    parser.add_argument('--case', help='Exact case ID; list with --list-cases')
    parser.add_argument('--list-cases', action='store_true')
    parser.add_argument('--from-date', help='Inclusive ISO execution date')
    parser.add_argument('--to-date', help='Inclusive ISO execution date')
    parser.add_argument('--event', help='Case-insensitive substring, e.g. STATE_CHANGE, DIP, EXIT')
    parser.add_argument('--asset', help='Asset on event or any linked trade, e.g. TQQQ')
    parser.add_argument('--limit', type=int, default=50, help='Maximum printed events; 0 means all')
    args = parser.parse_args(argv)
    try:
        if args.limit < 0:
            raise ValueError('limit must be nonnegative')
        if args.list_cases:
            print('\n'.join(case_names(load_manifest(args.run))))
            return 0
        if not args.case:
            raise ValueError('--case is required unless --list-cases is used')
        config, records = collect(args.run, args.case, from_date=args.from_date,
                                  to_date=args.to_date, event=args.event, asset=args.asset)
        shown = records if args.limit == 0 else records[:args.limit]
        print(json.dumps({'case': args.case, 'matched_events': len(records), 'shown_events': len(shown),
                          'config': config, 'integrity': 'selected_files_match_manifest_not_data_certification'},
                         ensure_ascii=False, allow_nan=False))
        for record in shown:
            print(json.dumps(record, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Review failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
