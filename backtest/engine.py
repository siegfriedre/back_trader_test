"""Self-financing, fractional-dollar sleeve ledger with next-close execution.

Prices represent adjusted return indices, NOT historical dollar/share quotes.
External cash arrives at the close after the day's existing-position returns.
Signals from close t execute at t+lag; terminal pending signals are NOT filled.
"""
from dataclasses import dataclass
import logging
import math

import numpy as np
import pandas as pd

from .config import Config, PROFILES, STRATEGIES
from .data import ASSETS, Dataset, indicators, window_start

LEDGER = ASSETS + ('USD',)
LEV = np.array([1., 3., 2., 0., 0.])
USD, SGOV = 4, 3
LOG = logging.getLogger('comparison')


@dataclass
class Result:
    strategy: str
    profile: str
    window: str
    daily: pd.DataFrame
    trades: list
    events: list
    cashflows: list
    config: dict

    @property
    def case_id(self):
        return f'{self.window}__{self.strategy}__{self.profile}'


def signal_states(signal, cfg, strategy):
    """Sequential hysteresis/confirmation, computed before the execution lag."""
    state, candidate, count, bull, tier = 'WAIT', '', 0, False, 'DEFENSIVE'
    states = []
    for row in signal.itertuples():
        if not row.ready:
            states.append('WAIT'); continue
        if strategy in ('hold_qqq', 'hold_leveraged'):
            desired = 'AGGRESSIVE'
        else:
            if row.QQQ > row.MA * (1 + cfg.trend_band_bps / 10000):
                bull = True
            elif row.QQQ < row.MA * (1 - cfg.trend_band_bps / 10000):
                bull = False
            if row.ROC < -cfg.roc_band_pct:
                tier = 'AGGRESSIVE'
            elif row.ROC > cfg.roc_band_pct:
                tier = 'DEFENSIVE'
            desired = ('AGGRESSIVE' if strategy == 'ma200' else tier) if bull else 'BEAR'
        if desired == candidate:
            count += 1
        else:
            candidate, count = desired, 1
        if count >= cfg.confirm_days:
            state = desired
        states.append(state)
    return np.asarray(states)


def base_weights(strategy, profile, state):
    w = np.zeros(5)
    if state == 'WAIT':
        w[USD] = 1
    elif state == 'BEAR':
        w[SGOV] = 1
    elif strategy == 'hold_qqq' or state == 'DEFENSIVE':
        w[0], w[SGOV] = .70, .30
    else:
        asset, fraction = PROFILES[profile]
        w[LEDGER.index(asset)], w[SGOV] = fraction, 1 - fraction
    return w


def run(data: Dataset, cfg: Config, strategy: str, profile: str, window: str,
        *, start=None, verbose=False) -> Result:
    if strategy not in STRATEGIES or profile not in PROFILES:
        raise ValueError('Unknown strategy/profile')
    sig = indicators(data, cfg)
    states = signal_states(sig, cfg, strategy)
    idx = data.prices.index
    requested_start = max(window_start(data, window), pd.Timestamp(start)) if start else window_start(data, window)
    first = int(idx.searchsorted(requested_start))
    if first >= len(idx):
        raise ValueError('No data at/after requested window start')
    core = np.array([0., 0., 0., 0., cfg.initial])
    lots, trades, events, days = [], [], [], []
    flows = [(idx[first], -cfg.initial)]
    shares = cfg.initial  # unitized portfolio NAV = 1 before initial execution costs
    total_in = cfg.initial
    current_state, invested, armed = 'WAIT', False, True
    lot_seq = 0
    arrays = data.returns.reindex(columns=ASSETS).to_numpy(float)
    synth = data.synthetic.reindex(columns=ASSETS).to_numpy(bool)
    price_array = data.prices.reindex(columns=ASSETS).to_numpy(float)
    source_array = data.sources.reindex(columns=ASSETS).to_numpy(object)
    c = cfg.cost_bps / 10000
    context = {}

    def aggregate():
        out = core.copy()
        for lot in lots:
            out[lot['asset']] += lot['value']
        return out

    def event(kind, **detail):
        record = {'event_id': len(events) + 1, **context, 'event': kind, **detail}
        events.append(record)
        if verbose:
            LOG.info('%s %s %s', context.get('execution_date'), kind, detail)
        return record['event_id']

    def record_trades(before, after, reason, execution_id):
        E0, E1 = float(before.sum()), float(after.sum())
        for a in range(4):
            delta = float(after[a] - before[a])
            if abs(delta) <= 1e-10 * max(1, E0):
                continue
            trades.append({**context, 'execution_id': execution_id, 'asset': LEDGER[a],
                           'side': 'BUY' if delta > 0 else 'SELL', 'notional': abs(delta),
                           'signed_notional': delta, 'cost': abs(delta) * c,
                           'weight_before': float(before[a] / E0), 'weight_after': float(after[a] / E1),
                           'equity_before': E0, 'equity_after': E1, 'reason': reason,
                           'price_kind': 'adjusted_return_index_not_dollar_share_price',
                           'price_index': float(price_array[i, a]), 'return_source': str(source_array[i, a]),
                           'synthetic_return_on_execution_day': bool(synth[i, a])})

    def rebalance(w, reason):
        nonlocal core
        before = aggregate()
        locked = before - core
        E, T = float(before.sum()), float(locked.sum())
        def desired(net):
            # Tactical positions keep their market value. If they appreciate so
            # much that the base no longer fits, reduce base risk, never borrow.
            out = locked.copy()
            equity_weights = w.copy(); equity_weights[SGOV] = equity_weights[USD] = 0
            amount = min(net * equity_weights.sum(), max(0., net - T))
            if equity_weights.sum():
                out += equity_weights / equity_weights.sum() * amount
            out[SGOV if w[USD] == 0 else USD] += max(0., net - out.sum())
            return out
        lo, hi = T, E
        for _ in range(65):
            mid = (lo + hi) / 2
            fee = c * np.abs(desired(mid)[:4] - before[:4]).sum()
            if mid + fee > E:
                hi = mid
            else:
                lo = mid
        target = desired((lo + hi) / 2)
        if abs(E - target.sum() - c * np.abs(target[:4] - before[:4]).sum()) > 1e-8 * max(1, E):
            raise ValueError('Unable to self-finance rebalance; tactical positions exhausted cash')
        core = target - locked
        core[np.abs(core) < 1e-10 * max(1, E)] = 0.
        exid = event(reason, target_weights=dict(zip(LEDGER, map(float, target / target.sum()))))
        record_trades(before, aggregate(), reason, exid)

    def invest_new_cash(w):
        """state_only: allocate external USD to underweights, no forced sales."""
        nonlocal core
        if core[USD] <= 0:
            return
        before = aggregate()
        target = w * before.sum()
        # Tactical overlay is not offset by selling core equities.
        deficit = np.maximum(target[:4] - core[:4], 0.)
        if deficit.sum() == 0:
            deficit[SGOV] = 1.
        budget = core[USD]
        spend = budget * deficit / deficit.sum()
        core[:4] += spend / (1 + c)
        core[USD] = 0.
        exid = event('CONTRIBUTION_ALLOCATION', budget=float(budget))
        record_trades(before, aggregate(), 'CONTRIBUTION_ALLOCATION', exid)

    def close_lot(lot, reason):
        before = aggregate()
        sale = lot['value']
        lots.remove(lot)
        core[SGOV] += sale * (1 - c) / (1 + c)
        exid = event(reason, lot_id=lot['id'], asset=LEDGER[lot['asset']], held_sessions=i - lot['entry_i'])
        record_trades(before, aggregate(), reason, exid)

    for i in range(first, len(idx)):
        date, k = idx[i], i - cfg.signal_lag
        row = sig.iloc[k] if k >= 0 else None
        context = {'execution_date': str(date.date()),
                   'signal_date': str(idx[k].date()) if k >= 0 else None,
                   'QQQ': float(row.QQQ) if row is not None and pd.notna(row.QQQ) else None,
                   'MA200': float(row.MA) if row is not None and pd.notna(row.MA) else None,
                   'RSI6': float(row.RSI) if row is not None and pd.notna(row.RSI) else None,
                   'ROC35_pct': float(row.ROC) if row is not None and pd.notna(row.ROC) else None}
        if i == first:
            event('INITIAL_CAPITAL', amount=cfg.initial)
        before_return = aggregate()
        prior_equity = float(before_return.sum())
        synthetic_weight = float((before_return[:4] * synth[i]).sum() / prior_equity)
        if window == 'all_observed' and synthetic_weight > 1e-12:
            raise ValueError('All-observed window would consume a synthetic held-asset return')
        if window == 'post_tqqq' and (before_return[:3] * synth[i, :3]).sum() > 1e-8:
            raise ValueError('Post-TQQQ window would consume synthetic equity returns')
        for a in range(4):
            value = before_return[a]
            if value != 0 and (not np.isfinite(arrays[i, a]) or arrays[i, a] <= -1):
                raise ValueError(f'{date.date()} {LEDGER[a]}: unavailable/ruined held-asset return')
            if core[a] != 0:
                core[a] *= 1 + arrays[i, a]
            for lot in lots:
                if lot['asset'] == a:
                    lot['value'] *= 1 + arrays[i, a]
        preflow = float(aggregate().sum())
        new_month = i > first and date.to_period('M') != idx[i - 1].to_period('M')
        contribution = cfg.monthly if new_month else 0.
        if contribution:
            shares += contribution / (preflow / shares)
            core[USD] += contribution
            total_in += contribution
            flows.append((date, -contribution))
            event('MONTHLY_CONTRIBUTION', amount=contribution)
        target_state = states[k] if k >= 0 else 'WAIT'
        available = np.isfinite(price_array[i]).all()
        if target_state != 'WAIT' and not available:
            raise ValueError('Ready signals but a required execution price is unavailable')
        # Original description explicitly starts with the aggressive allocation;
        # apply exactly once, then regular regimes apply on the next decision.
        if strategy == 'user_rules' and not invested and target_state != 'WAIT':
            target_state = 'AGGRESSIVE'
        changed = target_state != current_state
        if changed:
            old = current_state
            if lots:
                event('TACTICAL_RESET_ON_STATE_CHANGE', lot_ids=[x['id'] for x in lots])
                for lot in lots:
                    core[lot['asset']] += lot['value']
                lots.clear()  # reclassification; only NET instrument trades pay costs
            current_state = target_state
            event('STATE_CHANGE', before=old, after=current_state)
            rebalance(base_weights(strategy, profile, current_state), 'INITIAL_ALLOCATION' if not invested else 'REGIME_REBALANCE')
            if current_state != 'WAIT':
                invested = True
        if current_state != 'WAIT':
            w = base_weights(strategy, profile, current_state)
            if strategy in ('bull_rsi', 'bull_bear_rsi'):
                for lot in list(lots):
                    if row.RSI >= cfg.exit_rsi or i - lot['entry_i'] >= cfg.max_hold:
                        close_lot(lot, 'TACTICAL_RSI_EXIT' if row.RSI >= cfg.exit_rsi else 'TACTICAL_TIME_EXIT')
            if new_month and not changed:
                if cfg.rebalance == 'monthly':
                    rebalance(w, 'MONTHLY_REBALANCE')
                elif contribution:
                    invest_new_cash(w)
            # Consume every oversold excursion even if entry is blocked by a
            # state change or no cash. Re-arm only after RSI >= reset threshold.
            if row.RSI >= cfg.reset_rsi:
                armed = True
            crossed = k > 0 and pd.notna(sig.RSI.iloc[k - 1]) and sig.RSI.iloc[k - 1] >= cfg.entry_rsi and row.RSI < cfg.entry_rsi
            trigger = crossed and armed
            if trigger:
                armed = False
            enabled = strategy in ('bull_rsi', 'bull_bear_rsi', 'user_rules')
            if trigger and enabled:
                if changed:
                    event('DIP_SKIPPED_STATE_CHANGE')
                elif current_state == 'BEAR' and strategy == 'bull_rsi':
                    event('DIP_SKIPPED_BEAR_DISABLED')
                else:
                    equity = float(aggregate().sum())
                    original = strategy == 'user_rules'
                    asset = ('QQQ' if current_state == 'BEAR' else
                             PROFILES[profile][0] if original or current_state == 'AGGRESSIVE' else 'QQQ')
                    a = LEDGER.index(asset)
                    requested = cfg.dip_fraction * (core[SGOV] if original and current_state != 'BEAR' else equity)
                    room = equity if original else max(0., cfg.dip_cap * equity - sum(x['value'] for x in lots))
                    # Limited by available cash, including SELL and BUY costs.
                    bought = min(requested, room, core[SGOV] * (1 - c) / (1 + c))
                    if bought <= 1e-10 * equity:
                        event('DIP_SKIPPED_NO_CASH_OR_CAP', requested=float(requested))
                    else:
                        before = aggregate()
                        core[SGOV] -= bought * (1 + c) / (1 - c)
                        lot_seq += 1
                        lots.append({'id': lot_seq, 'asset': a, 'value': bought, 'entry_i': i})
                        exid = event('BEAR_DIP_BUY' if current_state == 'BEAR' else 'BULL_DIP_BUY',
                                     lot_id=lot_seq, asset=asset, requested=float(requested), filled=float(bought))
                        record_trades(before, aggregate(), 'TACTICAL_ENTRY', exid)
        elif i == first:
            event('WARMUP_IN_USD', detail='All cases wait for all indicators; uninvested USD earns 0%.')
        held = aggregate()
        equity = float(held.sum())
        if not np.isfinite(held).all() or equity <= 0 or held.min() < -1e-8 * equity:
            raise ValueError('Portfolio insolvency/nonfinite values/negative cash; no implicit borrowing')
        nav = equity / shares
        signal_proxy = bool(data.synthetic.VTV.iloc[max(0, k - cfg.roc_period):k + 1].any()) if k >= 0 else False
        days.append({'Date': date, 'Equity': equity, 'Contributed': total_in, 'Contribution': contribution,
                     'UnitNAV': nav, 'State': current_state, 'SignalDate': context['signal_date'],
                     'MA200': context['MA200'], 'RSI6': context['RSI6'], 'ROC35_pct': context['ROC35_pct'],
                     'ApproxDailyExposure': float(held @ LEV / equity),
                     'SyntheticReturnExposure': synthetic_weight, 'ProxyInROCSignal': signal_proxy,
                     'TacticalValue': float(sum(x['value'] for x in lots)),
                     **{f'Value_{a}': float(held[j]) for j, a in enumerate(LEDGER)},
                     **{f'Weight_{a}': float(held[j] / equity) for j, a in enumerate(LEDGER)}})
    event('END_MARK_TO_MARKET', pending_signals_not_executed=cfg.signal_lag,
          detail='No forced final liquidation; terminal holdings valued at final close.')
    daily = pd.DataFrame(days).set_index('Date')
    daily['DailyTWR'] = daily.UnitNAV.div(daily.UnitNAV.shift(1).fillna(1)) - 1
    daily['Drawdown'] = daily.UnitNAV / np.maximum.accumulate(np.r_[1., daily.UnitNAV])[1:] - 1
    return Result(strategy, profile, window, daily, trades, events, flows, cfg.to_dict())
