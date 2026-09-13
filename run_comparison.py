"""Run offline strategy comparisons, or read an existing research snapshot.

Examples (PowerShell / bash):
  python run_comparison.py --list
  python run_comparison.py --accept-legacy-adjusted
  python run_comparison.py --accept-legacy-adjusted --windows post_tqqq --profiles tqqq70 qld70
  python run_comparison.py --source research --end 2026-08-01
  python run_comparison.py --verify-run results/comparisons/RUN_ID
"""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from backtest.config import Config, DEFAULT_STRATEGIES, PROFILES, STRATEGIES, WINDOWS
from backtest.data import load_legacy, load_research, sha_bytes
from backtest.engine import run
from backtest.report import save_case, write_json, write_overview

ROOT = Path(__file__).resolve().parent


def verify_run(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'passed':
        raise ValueError('Run is incomplete/failed')
    inventory = manifest['output_sha256']
    if not inventory or 'summary.csv' not in inventory or 'report.html' not in inventory:
        raise ValueError('Incomplete output inventory')
    expected_paths = set(inventory) | {'manifest.json'}
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual != expected_paths:
        raise ValueError('Output inventory changed')
    for rel, expected in inventory.items():
        file = root / rel
        if not file.resolve().is_relative_to(root) or sha_bytes(file.read_bytes()) != expected:
            raise ValueError(f'Checksum/path mismatch: {rel}')
    print('Output checksums passed (not a market-data authenticity certification).')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--list', action='store_true', help='List strategy/profile/window names')
    parser.add_argument('--verify-run', type=Path)
    parser.add_argument('--source', choices=['legacy', 'research'], default='legacy')
    parser.add_argument('--raw-dir', type=Path, default=ROOT / 'data/raw')
    parser.add_argument('--research-dir', type=Path, default=ROOT / 'data/research')
    parser.add_argument('--signal-source', choices=['legacy_adjusted', 'split_close'], default='legacy_adjusted',
                        help='split_close separates non-dividend-adjusted signal quotes from reinvested returns')
    parser.add_argument('--signal-dir', type=Path, default=ROOT / 'data/signal_prices')
    parser.add_argument('--bridge-weight', type=float, help='QQQ allocation below MA after ROC crossunder (default .70)')
    parser.add_argument('--scenario', default='base', choices=['base', 'higher_financing', 'broad_equity_proxy', 'combined_stress'])
    parser.add_argument('--accept-legacy-adjusted', action='store_true')
    parser.add_argument('--end', help='Exclusive date; default common last observation + 1 day for legacy')
    parser.add_argument('--start', help='Optional later start; does not discard earlier indicator warm-up')
    parser.add_argument('--windows', nargs='+', choices=WINDOWS, default=list(WINDOWS))
    parser.add_argument('--strategies', nargs='+', choices=tuple(STRATEGIES), default=list(DEFAULT_STRATEGIES))
    parser.add_argument('--profiles', nargs='+', choices=tuple(PROFILES), default=list(PROFILES))
    parser.add_argument('--initial', type=float)
    parser.add_argument('--monthly', type=float)
    parser.add_argument('--cost-bps', type=float)
    parser.add_argument('--signal-lag', type=int)
    parser.add_argument('--confirm-days', type=int)
    parser.add_argument('--rebalance', choices=['monthly', 'state_only'])
    parser.add_argument('--value-proxy', choices=['VIVAX', 'SPY'])
    parser.add_argument('--funding-spread', type=float)
    parser.add_argument('--config', type=Path, help='JSON overriding Config fields; explicit CLI args take priority')
    parser.add_argument('--output', type=Path, default=ROOT / 'results/comparisons')
    parser.add_argument('--verbose-trades', action='store_true', help='Also print events to terminal/run.log; JSONL always written')
    args = parser.parse_args(argv)
    if args.list:
        print('\n'.join(f'{k}: {v}' for k, v in STRATEGIES.items()))
        print('\nProfiles:', PROFILES, '\nWindows:', WINDOWS)
        return 0
    out = None
    handlers = []
    log = logging.getLogger('comparison')
    try:
        if args.verify_run:
            verify_run(args.verify_run); return 0
        params = json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else {}
        if not isinstance(params, dict):
            raise ValueError('Configuration must be a JSON object')
        for field in ('initial', 'monthly', 'cost_bps', 'signal_lag', 'confirm_days', 'rebalance', 'value_proxy', 'funding_spread', 'bridge_weight'):
            if getattr(args, field) is not None:
                params[field] = getattr(args, field)
        cfg = Config(**params)
        for selections in (args.windows, args.strategies, args.profiles):
            if len(selections) != len(set(selections)):
                raise ValueError('Duplicate window/strategy/profile selection')
        model_fields = ('value_proxy', 'funding_spread', 'fund_fee', 'cash_fee', 'rate_lag_days', 'max_rate_age')
        if args.source == 'research' and any(getattr(cfg, f) != getattr(Config(), f) for f in model_fields):
            raise ValueError('Research model parameters belong to its snapshot; select --scenario instead')
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:8]
        out = args.output / run_id
        out.mkdir(parents=True)
        handlers = [logging.FileHandler(out / 'run.log', encoding='utf-8'), logging.StreamHandler()]
        for handler in handlers:
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            log.addHandler(handler)
        log.setLevel(logging.INFO); log.propagate = False
        log.warning('Research only: no live orders, no parameter search, no certified data.')
        data = (load_legacy(args.raw_dir, cfg, end=args.end, acknowledge=args.accept_legacy_adjusted)
                if args.source == 'legacy' else load_research(args.research_dir, args.end, args.scenario))
        if 'bear_roc_bridge' in args.strategies and args.signal_source != 'split_close':
            raise ValueError('bear_roc_bridge requires --signal-source split_close; do not reuse legacy adjusted signals')
        if args.signal_source == 'split_close':
            from backtest.data import indicators
            from backtest.signal_prices import attach_snapshot, tradingview_reference_check
            before = indicators(data, cfg)
            data = attach_snapshot(data, args.signal_dir)
            after = indicators(data, cfg)
            difference = before[['ROC', 'RSI', 'ROCCrossDown']].add_prefix('Adjusted_').join(
                after[['ROC', 'RSI', 'ROCCrossDown']].add_prefix('SplitClose_'))
            difference['Adjusted_AboveMA'] = before.QQQ > before.MA
            difference['SplitClose_AboveMA'] = after.QQQ > after.MA
            difference['ROCSignDiffers'] = before.ROC.lt(0) != after.ROC.lt(0)
            difference.to_csv(out / 'signal_comparison.csv', index_label='Date', date_format='%Y-%m-%d', encoding='utf-8-sig')
            reference = tradingview_reference_check(data, cfg)
            write_json(out / 'tradingview_reference_check.json', reference)
            data.audit['tradingview_reference_check'] = reference
            write_json(out / 'data_audit.json', data.audit)
            if reference['status'] == 'mismatch':
                raise ValueError('Downloaded signals do not match the TradingView screenshot; see tradingview_reference_check.json. No trades published.')
            log.info('Signal price basis: %s; one-day TradingView check: %s', data.signal_basis, reference['status'])

        write_json(out / 'data_audit.json', data.audit)
        # Persist the exact working returns, input signals and provenance for review.
        from backtest.data import indicators
        panel = data.prices.add_prefix('Index_').join(data.returns.add_prefix('Return_')).join(
            data.sources.add_prefix('Source_')).join(data.synthetic.add_prefix('Synthetic_')).join(
            indicators(data, cfg).add_prefix('Signal_'))
        panel.to_csv(out / 'input_panel.csv', index_label='Date', date_format='%Y-%m-%d', encoding='utf-8-sig')
        manifest = {'status': 'running', 'schema': 1, 'run_id': run_id, 'configuration': cfg.to_dict(),
                    'source_mode': data.audit['source_mode'], 'source_audit_sha256': sha_bytes((out / 'data_audit.json').read_bytes()),
                    'signal_source': args.signal_source, 'signal_basis': data.signal_basis,
                    'execution': 'previous_close_signal_next_close_fractional_return_indices',
                    'windows': args.windows, 'strategies': args.strategies, 'profiles': args.profiles,
                    'start_override': args.start, 'end_exclusive': data.audit['effective_end_exclusive'],
                    'scenario': args.scenario if args.source == 'research' else 'legacy_explicit_assumptions',
                    'versions': {n: version(n) for n in ('numpy', 'pandas', 'exchange-calendars')},
                    'python': sys.version, 'code_sha256': {p.relative_to(ROOT).as_posix(): sha_bytes(p.read_bytes())
                        for p in sorted(list((ROOT / 'backtest').glob('*.py')) + [Path(__file__), ROOT / 'research_model.py'])}}
        try:
            manifest['git_head'] = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        except (OSError, subprocess.SubprocessError):
            manifest['git_head'] = None
        write_json(out / 'manifest.json', manifest)
        summaries, annual, crisis = [], [], []
        for window in args.windows:
            for strategy in args.strategies:
                # QQQ benchmark does not depend on leveraged profile; avoid duplicate cases.
                for profile in ([args.profiles[0]] if strategy == 'hold_qqq' else args.profiles):
                    log.info('Running %s / %s / %s', window, strategy, profile)
                    result = run(data, cfg, strategy, profile, window, start=args.start, verbose=args.verbose_trades)
                    s, a, cr = save_case(result, out)
                    summaries.append(s); annual.extend(a); crisis.extend(cr)
                    log.info('Finished: final=%.2f contributed=%.2f maxDD=%.2f%% trades=%d',
                             s['final_equity'], s['contributed'], s['max_drawdown'] * 100, s['trade_legs'])
        write_overview(out, summaries, annual, crisis, data.audit)
        log.info('Completed %d cases. Open %s', len(summaries), out / 'report.html')
        for handler in handlers:
            handler.flush(); handler.close(); log.removeHandler(handler)
        handlers.clear()
        manifest.update(status='passed', completed_at_utc=datetime.now(timezone.utc).isoformat(), cases=len(summaries))
        manifest['output_sha256'] = {p.relative_to(out).as_posix(): sha_bytes(p.read_bytes())
                                     for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'manifest.json'}
        write_json(out / 'manifest.json', manifest)
        print(f'RESULT_DIR={out.resolve()}')
        return 0
    except Exception as exc:
        if out:
            old = {}
            if (out / 'manifest.json').exists():
                old = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
            old.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            write_json(out / 'manifest.json', old)
        print(f'FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    finally:
        for handler in handlers:
            handler.close(); log.removeHandler(handler)


if __name__ == '__main__':
    raise SystemExit(main())
