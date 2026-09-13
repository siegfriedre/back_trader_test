"""Independent split-adjusted, NON-dividend-adjusted signal-price snapshots.

Never reverse-engineer missing unadjusted quotes from legacy adjusted CSVs.
Snapshots record Yahoo's returned Close/Adj Close/actions and metadata. They
are not a TradingView feed, and a one-day reference match is not full-history
certification. Attaching signals never changes the portfolio return series.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd

from .data import Dataset, aligned, check_index, indicators, join_history, sessions, sha_bytes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'data/signal_prices'
NAMES = ('QQQ', 'VTV', 'VIVAX')
BASIS = 'split_close_no_dividend_adjustment'


def _expected_files():
    return {f'{folder}/{n}{suffix}' for n in NAMES for folder, suffix in
            [('raw', '.csv'), ('raw', '.metadata.json'), ('prices', '.csv')]}


def prepare_snapshot(start: str, end: str, output: Path = DEFAULT_OUTPUT, *, fetcher=None) -> Path:
    """All-or-nothing batch. Existing inputs and latest failed status are preserved."""
    import get_data as gd
    from prepare_research import validate_supplement
    gd.dates(start, end)
    output = Path(output)
    fetcher = fetcher or gd.fetch_yahoo
    with gd.snapshot_lock(output):
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:10]
        run = output / 'runs' / run_id
        (run / 'raw').mkdir(parents=True)
        (run / 'prices').mkdir()
        pointer = dict(schema=1, run_id=run_id, status='downloading')
        gd.atomic_json(output / 'latest.json', pointer)
        manifest = dict(schema=1, run_id=run_id, status='downloading', basis=BASIS,
                        start_inclusive=start, end_exclusive=end, symbols=list(NAMES),
                        provider='Yahoo Finance via yfinance', tradingview_feed=False,
                        independently_verified=False, files={}, instruments={},
                        history_options=gd.HISTORY_OPTIONS,
                        code_sha256=sha_bytes(Path(__file__).read_bytes()))
        try:
            index = gd.trading_sessions(start, end)
            for name in NAMES:
                print(f'Signal quotes: downloading {name} (auto_adjust=False)...', flush=True)
                response, meta = fetcher(name, start, end)
                response.to_csv(run / 'raw' / f'{name}.csv', index_label='Date')
                (run / 'raw' / f'{name}.metadata.json').write_text(
                    json.dumps(meta, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
                frame = gd.normalize_daily(response)
                if name == 'VIVAX':
                    validate_supplement(frame, meta, name, index)
                else:
                    gd.validate_raw(frame, meta, name, index)
                if name in ('QQQ', 'VIVAX') and frame.index[0] != index[0]:
                    raise ValueError(f'{name}: source must cover requested research start')
                frame.to_csv(run / 'prices' / f'{name}.csv', index_label='Date', date_format='%Y-%m-%d')
                manifest['instruments'][name] = {
                    'first_date': str(frame.index[0].date()), 'last_date': str(frame.index[-1].date()),
                    'rows': len(frame), 'fetched_at_utc': datetime.now(timezone.utc).isoformat()}
            manifest['files'] = {rel: sha_bytes((run / rel).read_bytes()) for rel in sorted(_expected_files())}
            manifest['status'] = 'passed'
            gd.atomic_json(run / 'manifest.json', manifest)
            pointer.update(status='passed', manifest_sha256=sha_bytes((run / 'manifest.json').read_bytes()))
            gd.atomic_json(output / 'latest.json', pointer)
        except BaseException as exc:
            pointer.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            manifest.update(status='failed', error=pointer['error'])
            gd.atomic_json(output / 'latest.json', pointer)
            gd.atomic_json(run / 'manifest.json', manifest)
            raise
    return run


def read_snapshot(output: Path, *, end: str) -> tuple[dict, dict]:
    """Reject stale, partial, modified and in-progress snapshots; no legacy fallback."""
    root = Path(output)
    if (root / '.download.lock').exists():
        raise ValueError('Signal-price snapshot is being prepared')
    try:
        initial = (root / 'latest.json').read_bytes()
        pointer = json.loads(initial)
        run_id = pointer['run_id']
        if (pointer.get('status') != 'passed' or pointer.get('schema') != 1
                or not isinstance(run_id, str) or Path(run_id).name != run_id or run_id in ('.', '..')):
            raise ValueError('Latest signal-price preparation failed/incomplete')
        run = root / 'runs' / run_id
        if not run.resolve().is_relative_to((root / 'runs').resolve()):
            raise ValueError('Unsafe signal snapshot path')
        raw_manifest = (run / 'manifest.json').read_bytes()
        if sha_bytes(raw_manifest) != pointer['manifest_sha256']:
            raise ValueError('Signal-price manifest checksum mismatch')
        manifest = json.loads(raw_manifest)
        if (manifest.get('status') != 'passed' or manifest.get('schema') != 1
                or manifest.get('run_id') != run_id or manifest.get('basis') != BASIS
                or manifest.get('end_exclusive') != end or manifest.get('symbols') != list(NAMES)):
            raise ValueError('Signal-price contract/date mismatch')
        if set(manifest['files']) != _expected_files():
            raise ValueError('Signal-price inventory incomplete')
        frames = {}
        for rel, digest in manifest['files'].items():
            path = run / rel
            content = path.read_bytes()
            if not path.resolve().is_relative_to(run.resolve()) or sha_bytes(content) != digest:
                raise ValueError(f'Signal-price checksum/path mismatch: {rel}')
            # Parse exactly the bytes whose digest was checked, not a second file read.
            if rel.startswith('prices/'):
                from io import BytesIO
                name = Path(rel).stem
                frame = pd.read_csv(BytesIO(content), index_col='Date', parse_dates=['Date'])
                check_index(frame.index, name)
                if frame.columns.has_duplicates or not {'Close', 'Adj Close', 'Dividends', 'Stock Splits'} <= set(frame):
                    raise ValueError(f'{name}: invalid signal quote schema')
                values = frame[['Close', 'Adj Close', 'Dividends', 'Stock Splits']].to_numpy(float)
                if not np.isfinite(values).all() or (values[:, :2] <= 0).any() or (values[:, 2:] < 0).any():
                    raise ValueError(f'{name}: invalid signal quotes/actions')
                frames[name] = frame
        if initial != (root / 'latest.json').read_bytes() or (root / '.download.lock').exists():
            raise ValueError('Signal-price snapshot changed during read')
        return frames, {'pointer': pointer, 'manifest': manifest, 'source_path': str(root)}
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError(f'Signal prices unavailable: {exc}. Run prepare_signal_prices.py first.') from exc


def attach_quotes(data: Dataset, frames: dict, provenance: dict) -> Dataset:
    """Preserve returns exactly; only replace the indicator inputs.

    VTV before its first observation uses VIVAX's NON-dividend-adjusted Close
    returns, not the previous total-return proxy. A constant display scaling
    aligns the resulting index with observed VTV quotes. This scaling cancels
    out of every ratio ROC; it is not return fitting or a trading signal.
    """
    if set(frames) != set(NAMES):
        raise ValueError('QQQ, VTV and VIVAX quote frames required')
    index = data.prices.index
    close = {n: aligned(frames[n].Close, index, n) for n in NAMES}
    for name in ('QQQ', 'VIVAX'):
        if not close[name].index.equals(index):
            raise ValueError(f'{name}: incomplete signal history')
    if any(not np.isfinite(s.to_numpy(float)).all() or (s <= 0).any() for s in close.values()):
        raise ValueError('Nonpositive/missing signal close; refusing fill')
    if close['VTV'].index[0] != data.observed_start['VTV']:
        raise ValueError('Observed VTV start differs between return and signal sources; investigate')
    level, _, source, synthetic = join_history(
        'VTV', close['VTV'], index, close['VIVAX'].pct_change(fill_method=None), 'proxy:VIVAX_split_close')
    # Multiplying the whole series by one constant cannot change ROC.
    level *= close['VTV'].iloc[0] / level.loc[close['VTV'].index[0]]
    np.testing.assert_allclose(level.loc[close['VTV'].index], close['VTV'], rtol=1e-10)
    signal_prices = pd.DataFrame({'QQQ': close['QQQ'], 'VTV': level}, index=index)
    sources = pd.DataFrame({'QQQ': 'observed:QQQ_split_close', 'VTV': source}, index=index)
    synthetic = pd.DataFrame({'QQQ': False, 'VTV': synthetic}, index=index)
    audit = deepcopy(data.audit)
    audit.update(signal_basis=BASIS, signal_price_snapshot=provenance,
                 return_basis='unchanged dividend-reinvested return indices; no extra dividend cash',
                 tradingview_full_history_certified=False)
    audit.setdefault('warnings', []).extend([
        '信号采用不做分红调整的 Close；这不是 TradingView 授权行情源。',
        '收益仍使用原有含分红复投近似的收益序列；未重复发放现金分红，尚非券商实际DRIP账本。',
        'VTV上市前信号使用VIVAX非分红调整Close代理；并非真实VTV历史。'])
    return replace(data, signal_prices=signal_prices, signal_sources=sources,
                   signal_synthetic=synthetic, signal_basis=BASIS, audit=audit)


def attach_snapshot(data: Dataset, root: Path) -> Dataset:
    end = data.audit['effective_end_exclusive']
    frames, provenance = read_snapshot(root, end=end)
    if not sessions(provenance['manifest']['start_inclusive'], end).equals(data.prices.index):
        raise ValueError('Signal-price start differs from return dataset')
    return attach_quotes(data, frames, provenance)


def tradingview_reference_check(data: Dataset, cfg) -> dict:
    """Single screenshot acceptance check; never an override of a price or signal.

    Screenshot gives only QQQ Close=584.31, ROC=-0.0089 (4 decimal display),
    and the crossunder label at 2026-04-01. It supplies NO MA200 or RSI value.
    """
    day = pd.Timestamp('2026-04-01')
    reference = {'date': '2026-04-01', 'QQQ_Close': 584.31,
                 'ROC35_pct_display': -0.0089, 'ROCCrossDown': True,
                 'source': 'user-supplied TradingView daily screenshot',
                 'scope': 'ONE_DAY_ONLY_NOT_FULL_HISTORY_CERTIFICATION'}
    if day not in data.prices.index or cfg.roc_period != 35:
        return dict(status='not_applicable', reference=reference)
    signal = indicators(data, cfg)
    row = signal.loc[day]
    tests = {
        'QQQ_Close_matches_display': bool(abs(row.QQQ - 584.31) <= .00501),
        'ROC_matches_four_decimal_display': bool(abs(row.ROC - (-.0089)) <= .0000501),
        'ROC_is_negative': bool(row.ROC < 0),
        'fresh_crossunder': bool(row.ROCCrossDown),
    }
    return dict(status='passed' if all(tests.values()) else 'mismatch', reference=reference,
                checks=tests, actual={'QQQ_Close': float(row.QQQ), 'ROC35_pct': float(row.ROC),
                                     'ROCReferenceDate': str(row.ROCReferenceDate.date()),
                                     'VTV_Close': float(row.VTV), 'MA200': float(row.MA),
                                     'below_MA': bool(row.QQQ < row.MA)},
                note='Do not change prices, length, zero threshold or dates to force this check to pass.')
