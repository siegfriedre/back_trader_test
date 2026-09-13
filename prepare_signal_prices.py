"""Download separate signal quotes; never overwrite existing return inputs.

python prepare_signal_prices.py --end 2026-08-01
"""
import argparse
from pathlib import Path
import sys
from backtest.signal_prices import DEFAULT_OUTPUT, prepare_snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='1999-03-10')
    parser.add_argument('--end', required=True, help='Exclusive ISO date, same as the comparison')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        run = prepare_snapshot(args.start, args.end, args.output)
        print(f'SIGNAL_PRICE_DIR={args.output.resolve()}\nSNAPSHOT={run}')
        print('Split-adjusted/no-dividend-adjustment signals; not a TradingView feed certification.')
        return 0
    except Exception as exc:
        print(f'Signal preparation FAILED: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
