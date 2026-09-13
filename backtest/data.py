"""Data adapters. Legacy input requires explicit acknowledgement; never certified.

Original *_synthetic_daily.csv files are deliberately NOT consumed. Reconstruct
needed pre-inception return series, retaining observed returns after the join.
"""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from io import BytesIO
import hashlib
import json

import numpy as np
import pandas as pd

from research_model import wilder_rsi
from .config import Config

ASSETS = ('QQQ', 'TQQQ', 'QLD', 'SGOV')


@dataclass
class Dataset:
    prices: pd.DataFrame
    returns: pd.DataFrame
    sources: pd.DataFrame
    synthetic: pd.DataFrame
    observed_start: dict
    audit: dict
    # Signal quotes are separate from dividend-reinvested valuation indices.
    signal_prices: pd.DataFrame | None = None
    signal_sources: pd.DataFrame | None = None
    signal_synthetic: pd.DataFrame | None = None
    signal_basis: str = 'adjusted_return_index_legacy'


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sessions(start, end):
    """end exclusive; tolerate calendar package tz representation differences."""
    import exchange_calendars as xc
    left, right = pd.Timestamp(start), pd.Timestamp(end)
    if left >= right:
        raise ValueError('Empty/reversed date interval')
    cal = xc.get_calendar('XNYS', start=left, end=right)
    idx = cal.sessions
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    idx = idx[(idx >= left) & (idx < right)]
    if idx.empty:
        raise ValueError('No exchange sessions')
    return idx.rename('Date')


def check_index(index, name):
    if (not isinstance(index, pd.DatetimeIndex) or index.empty or index.tz is not None
            or index.hasnans or index.has_duplicates or not index.is_monotonic_increasing
            or not index.equals(index.normalize())):
        raise ValueError(f'{name}: expected sorted unique, timezone-naive daily dates')


def load_raw(path, name, acknowledge):
    data = Path(path).read_bytes()
    frame = pd.read_csv(BytesIO(data), index_col='Date', parse_dates=['Date'])
    check_index(frame.index, name)
    if frame.columns.has_duplicates:
        raise ValueError(f'{name}: duplicate columns')
    if name == 'IRX':
        col, basis = 'Close', 'legacy_discount_yield_percent'
    elif 'Adj Close' in frame:
        col, basis = 'Adj Close', 'provider_adjusted_close_unverified'
    elif acknowledge:
        col, basis = 'Close', 'legacy_assumed_adjusted_close_UNVERIFIED'
    else:
        raise ValueError('Legacy Close has no adjustment metadata. Explicit --accept-legacy-adjusted required')
    if col not in frame:
        raise ValueError(f'{name}: missing {col}')
    close = pd.to_numeric(frame[col], errors='raise').astype(float)
    if not np.isfinite(close.to_numpy()).all() or (name != 'IRX' and (close <= 0).any()):
        raise ValueError(f'{name}: invalid observed close (never filled)')
    if name == 'IRX' and not close.between(-10, 100).all():
        raise ValueError('IRX must be annual percentage yield, not a price/return')
    return close, {'path': str(path), 'sha256': sha_bytes(data), 'basis': basis,
                   'first_observation': str(close.index[0].date()),
                   'last_observation': str(close.index[-1].date()), 'rows': len(close)}


def aligned(close, index, name):
    """Trim only explicitly requested date range; never repair an observed gap."""
    selected = close.loc[(close.index >= index[0]) & (close.index <= index[-1])]
    if selected.empty:
        raise ValueError(f'{name}: no observation inside requested history')
    expected = index[index >= selected.index[0]]
    if not selected.index.equals(expected):
        missing = expected.difference(selected.index).strftime('%Y-%m-%d').tolist()[:5]
        extra = selected.index.difference(expected).strftime('%Y-%m-%d').tolist()[:5]
        raise ValueError(f'{name}: missing/extra observed sessions: {missing} / {extra}')
    return selected


def rate_inputs(rate, index, cfg):
    """Use IRX known before previous close with fixed publication lag, not DFF.

    Leading unavailable quotes remain NaN, so model prices cannot be traded then.
    Missing/stale quotes later also remain NaN and will block affected models.
    """
    values, dates = [], []
    for prev in index[:-1]:
        cutoff = prev - pd.Timedelta(days=cfg.rate_lag_days)
        pos = rate.index.searchsorted(cutoff, side='right') - 1
        if pos < 0 or (prev - rate.index[pos]).days > cfg.max_rate_age:
            values.append(np.nan); dates.append(pd.NaT)
        else:
            values.append(rate.iloc[pos] / 100); dates.append(rate.index[pos])
    return pd.DataFrame({'rate': [np.nan] + values,
                         'observation_date': [pd.NaT] + dates,
                         'calendar_days': pd.Series(index, index=index).diff().dt.days}, index=index)


def join_history(name, observed, index, model=None, model_source=None):
    """Unanchored forward total-return index. First real close uses modeled bridge.

    Only leading unavailable model dates allowed. Actual target returns from its
    second observation are immutable. No future listing level sets old prices.
    """
    obs = aligned(observed, index, name)
    actual = obs.pct_change(fill_method=None).reindex(index)
    ret = pd.Series(np.nan, index=index, dtype=float)
    src = pd.Series('unavailable', index=index, dtype=object)
    first = index.get_loc(obs.index[0])
    if first:
        if model is None:
            raise ValueError(f'{name}: pre-inception model required')
        ret.iloc[:first + 1] = model.reindex(index).iloc[:first + 1]
        if isinstance(model_source, pd.Series):
            src.iloc[:first + 1] = model_source.reindex(index).iloc[:first + 1]
        else:
            src.iloc[:first + 1] = model_source
        src.iloc[first] = 'transition:' + str(src.iloc[first])
    ret.iloc[first + 1:] = actual.iloc[first + 1:]
    src.iloc[first + 1:] = 'observed:' + name
    available = ret.notna()
    if first == 0:
        anchor = 0
        src.iloc[0] = 'observed:' + name
    elif available.any():
        # one known initial level immediately before first known modeled return
        anchor = max(0, int(np.flatnonzero(available)[0]) - 1)
        src.iloc[anchor] = 'model_anchor:' + name
    else:
        raise ValueError(f'{name}: no usable returns')
    tail = ret.iloc[anchor + 1:]
    if not np.isfinite(tail.to_numpy()).all():
        raise ValueError(f'{name}: model or observed returns have a gap/stale rate')
    if (tail <= -1).any():
        raise ValueError(f'{name}: model ruin (<= -100%); refusing clipping/resurrection')
    level = pd.Series(np.nan, index=index, dtype=float)
    level.iloc[anchor:] = 100 * np.exp(np.r_[0., np.log1p(tail).cumsum()])
    if not np.isfinite(level.iloc[anchor:]).all() or (level.iloc[anchor:] <= 0).any():
        raise ValueError(f'{name}: numerical ruin/overflow')
    return level, ret, src, ~src.str.startswith('observed:')


def load_legacy(root, cfg, *, end=None, acknowledge=False):
    if not acknowledge:
        raise ValueError('Repository raw CSVs are legacy inputs, not verified snapshots. '
                         'Use --accept-legacy-adjusted only after accepting this limitation.')
    names = list(dict.fromkeys(['QQQ', 'TQQQ', 'QLD', 'VTV', 'SGOV', 'BIL', cfg.value_proxy, 'IRX']))
    raw, records = {}, {}
    for name in names:
        raw[name], records[name] = load_raw(Path(root) / f'{name}_raw.csv', name, acknowledge)
    first = raw['QQQ'].index[0]
    price_names = [n for n in names if n != 'IRX']
    effective_end = pd.Timestamp(end) if end else min(raw[n].index[-1] for n in price_names) + pd.Timedelta(days=1)
    today = pd.Timestamp(datetime.now(ZoneInfo('America/New_York')).date())
    if effective_end > today:
        raise ValueError('End must not exceed New York today (end exclusive)')
    idx = sessions(first, effective_end)
    close = {n: aligned(raw[n], idx, n) for n in price_names}
    if close['QQQ'].index[0] != idx[0] or close[cfg.value_proxy].index[0] != idx[0]:
        raise ValueError('QQQ and value proxy must cover research start')
    rates = rate_inputs(raw['IRX'], idx, cfg)
    qret = close['QQQ'].pct_change(fill_method=None).reindex(idx)
    d, dt = rates.rate, rates.calendar_days
    denominator = 360 - d * 91
    if (denominator.dropna() <= 0).any():
        raise ValueError('Invalid T-bill discount yield')
    bill_carry = d * dt / denominator
    cash_model = bill_carry - cfg.cash_fee * dt / 365
    cash_src = pd.Series('model:IRX_91day_bill_carry', index=idx)
    bilret = close['BIL'].pct_change(fill_method=None).reindex(idx)
    cash_model.loc[bilret.notna()] = bilret.loc[bilret.notna()]
    cash_src.loc[bilret.notna()] = 'proxy:BIL'
    # Short bill investment-yield approximation as funding benchmark, not actual swap rate.
    funding_cost = bill_carry + cfg.funding_spread * dt / 365
    models = {'SGOV': (cash_model, cash_src),
              'VTV': (close[cfg.value_proxy].pct_change(fill_method=None).reindex(idx), 'proxy:' + cfg.value_proxy)}
    for n, leverage in [('TQQQ', 3), ('QLD', 2)]:
        models[n] = (leverage * qret - (leverage - 1) * funding_cost - cfg.fund_fee * dt / 365,
                     f'model:QQQ_{leverage}x_IRX_funding')
    prices, returns, sources, synthetic = {}, {}, {}, {}
    for name in ['QQQ', 'VTV', 'TQQQ', 'QLD', 'SGOV']:
        model, src = models.get(name, (None, None))
        prices[name], returns[name], sources[name], synthetic[name] = join_history(name, close[name], idx, model, src)
    audit = {'source_mode': 'legacy_UNVERIFIED', 'independently_verified': False,
             'point_in_time_certified': False, 'adjustment_assumption_accepted': True,
             'requested_end_exclusive': end, 'effective_end_exclusive': str(effective_end.date()),
             'data_start': str(first.date()), 'files': records, 'funding_model': 'lagged_IRX_bill_yield_not_DFF',
             'assumptions': cfg.to_dict(),
             'warnings': ['旧 CSV 缺少复权/下载元数据；Close 按调整价假设使用，不等于认证行情。',
                          '不读取旧 synthetic CSV；仅上市前及首个观测日的衔接收益使用模型。',
                          '历史调整价不是 point-in-time 快照；基金选择和模型存在事后偏差。',
                          'IRX 不是实际基金融资利率；固定费用、利差和发布滞后仅为研究假设。',
                          '不同原始文件末日见 files；未指定 --end 时明确采用共同最早末日。'],
             'rate_selection': rates.reset_index().assign(
                 observation_date=lambda x: x.observation_date.dt.strftime('%Y-%m-%d'),
                 Date=lambda x: x.Date.dt.strftime('%Y-%m-%d')).replace({np.nan: None}).to_dict('records')}
    return Dataset(pd.DataFrame(prices), pd.DataFrame(returns), pd.DataFrame(sources),
                   pd.DataFrame(synthetic), {n: close[n].index[0] for n in close}, audit)


def load_research(root, end, scenario):
    """Use existing hash-checked research snapshots; never fall back to legacy."""
    if not end:
        raise ValueError('--end is required with --source research')
    from prepare_research import load_research_data
    pointer_bytes = (Path(root) / 'latest.json').read_bytes()
    frames = load_research_data(end=end, scenario=scenario, allow_synthetic=True,
                               allow_historical_backcast=True, execution='close_return_only', output=Path(root))
    names = ['QQQ', 'VTV', 'TQQQ', 'QLD', 'SGOV']
    first = {n: frames[n].ObservedAdjustedClose.first_valid_index() for n in names}
    if pointer_bytes != (Path(root) / 'latest.json').read_bytes():
        raise ValueError('Research snapshot changed during loading')
    pointer = json.loads(pointer_bytes)
    manifest_path = Path(root) / 'runs' / pointer['run_id'] / 'manifest.json'
    if sha_bytes(manifest_path.read_bytes()) != pointer['manifest_sha256']:
        raise ValueError('Research snapshot changed during loading')
    audit = {'source_mode': 'research_snapshot', 'pointer': pointer, 'source_path': str(root),
             'independently_verified': False, 'point_in_time_certified': False,
             'effective_end_exclusive': end, 'scenario': scenario,
             'warnings': ['来源为哈希校验研究批次；供应商观测也不等于独立核实。',
                          '研究批次中既有观测收益，也有上市前模拟；按行保留来源标记。']}
    return Dataset(pd.DataFrame({n: frames[n].ResearchClose for n in names}),
                   pd.DataFrame({n: frames[n].DailyReturn for n in names}),
                   pd.DataFrame({n: frames[n].ReturnSource for n in names}),
                   pd.DataFrame({n: frames[n].IsSyntheticReturn.astype(bool) for n in names}), first, audit)


def indicators(data, cfg):
    prices = data.signal_prices if data.signal_prices is not None else data.prices
    q, value = prices.QQQ, prices.VTV
    ratio = value / q
    lagged = ratio.shift(cfg.roc_period)
    out = pd.DataFrame({'QQQ': q, 'VTV': value,
                        'MA': q.rolling(cfg.ma_period).mean(),
                        'RSI': wilder_rsi(q, cfg.rsi_period),
                        'ROC': (ratio / lagged - 1) * 100,
                        'Ratio': ratio, 'ROCReferenceRatio': lagged,
                        'ROCReferenceDate': pd.Series(prices.index, index=prices.index).shift(cfg.roc_period)})
    out['ROCCrossDown'] = out.ROC.lt(0) & out.ROC.shift(1).ge(0)
    out['ROCCrossUp'] = out.ROC.gt(0) & out.ROC.shift(1).le(0)
    out['SignalPriceBasis'] = data.signal_basis
    source = data.signal_sources if data.signal_sources is not None else data.sources
    out['QQQSource'], out['VTVSource'] = source.QQQ, source.VTV
    out['ready'] = out[['QQQ', 'MA', 'RSI', 'ROC']].notna().all(axis=1)
    return out


def window_start(data, name):
    if name == 'long_history':
        return data.prices.index[0]
    if name == 'post_tqqq':
        return max(data.observed_start[n] for n in ('TQQQ', 'QLD', 'QQQ', 'VTV'))
    if name == 'all_observed':
        return max(data.observed_start[n] for n in ('TQQQ', 'QLD', 'QQQ', 'VTV', 'SGOV'))
    raise ValueError('Unknown window: ' + name)
