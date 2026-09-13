"""Invented fixtures unless explicitly identified as a screenshot display reference.

The April dates below test causal ordering, not observed historical performance.
No test fixture is ever used as a production quote file.
"""
import contextlib
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtest.bridge import BRIDGE, bridge_states
from backtest.config import Config
from backtest.data import Dataset, indicators, sessions
from backtest.engine import run
from backtest.signal_prices import (BASIS, attach_quotes, attach_snapshot, prepare_snapshot,
                                    read_snapshot, tradingview_reference_check)
import run_comparison as cli


def data_for(index):
    names = ['QQQ', 'VTV', 'TQQQ', 'QLD', 'SGOV']
    prices = pd.DataFrame(100., index=index, columns=names)
    returns = pd.DataFrame(0., index=index, columns=names)
    returns.iloc[0] = np.nan
    sources = pd.DataFrame({n: 'observed:' + n for n in names}, index=index)
    return Dataset(prices, returns, sources, pd.DataFrame(False, index=index, columns=names),
                   {n: index[0] for n in names}, {'source_mode': 'TEST_NOT_MARKET',
                   'effective_end_exclusive': str((index[-1] + pd.Timedelta(days=1)).date())},
                   signal_prices=prices[['QQQ', 'VTV']].copy(), signal_basis=BASIS)


def april_fixture():
    data = data_for(sessions('2026-03-25', '2026-04-15'))
    s = pd.DataFrame({'QQQ': 90., 'MA': 100., 'RSI': 40., 'ROC': 1., 'ready': True}, index=data.prices.index)
    s.loc['2026-04-01':, 'ROC'] = -.0089
    s.loc['2026-04-08':, 'QQQ'] = 110.
    return data, s


def execute(data, sig, cfg=None, strategy='bear_roc_bridge', profile='tqqq70', **kwargs):
    with patch('backtest.engine.indicators', return_value=sig):
        return run(data, cfg or Config(monthly=0, cost_bps=0), strategy, profile, 'long_history', **kwargs)


def quotes_fixture():
    index = sessions('2024-01-01', '2026-01-01')
    frames, meta = {}, {}
    for name in ('QQQ', 'VTV', 'VIVAX'):
        ix = index[20:] if name == 'VTV' else index
        close = 100. + np.arange(len(ix)) * .02
        frames[name] = pd.DataFrame({'Open': close, 'High': close + 1, 'Low': close - 1,
            'Close': close, 'Adj Close': close * .9, 'Volume': 0 if name == 'VIVAX' else 1000,
            'Dividends': 0., 'Stock Splits': 0., 'Capital Gains': 0.}, index=ix)
        meta[name] = {'symbol': name, 'currency': 'USD', 'exchangeTimezoneName': 'America/New_York',
            'instrumentType': 'MUTUALFUND' if name == 'VIVAX' else 'ETF',
            'firstTradeDate': int(ix[0].tz_localize('America/New_York').timestamp())}
    return index, frames, meta


class BridgeRulesTests(unittest.TestCase):
    def test_april_first_signal_executes_next_close_not_same_close(self):
        data, sig = april_fixture()
        out = execute(data, sig)
        event = next(e for e in out.events if e['event'] == 'BEAR_ROC_QQQ_ENTRY')
        self.assertEqual(event['signal_date'], '2026-04-01')
        self.assertEqual(event['execution_date'], '2026-04-02')
        self.assertAlmostEqual(out.daily.loc['2026-04-02', 'Weight_QQQ'], .7)
        self.assertEqual(out.daily.loc['2026-04-01', 'Weight_QQQ'], 0)
        self.assertTrue(out.daily.loc['2026-04-02':'2026-04-08', 'Weight_TQQQ'].eq(0).all())
        self.assertAlmostEqual(out.daily.loc['2026-04-09', 'Weight_TQQQ'], .7)
        self.assertEqual(out.daily.loc['2026-04-09', 'Weight_QQQ'], 0)

    def test_previous_user_rules_remain_available_and_unchanged(self):
        data, sig = april_fixture()
        old = execute(data, sig, strategy='user_rules')
        self.assertEqual(old.daily.loc['2026-04-02', 'State'], 'BEAR')
        self.assertEqual(old.daily.loc['2026-04-02', 'Weight_QQQ'], 0)
        self.assertFalse(any(e['event'] == 'BEAR_ROC_QQQ_ENTRY' for e in old.events))

    def test_bridge_holds_if_roc_turns_positive_before_ma(self):
        data, sig = april_fixture()
        sig.loc['2026-04-06':, 'ROC'] = 1
        out = execute(data, sig)
        self.assertEqual(out.daily.loc['2026-04-08', 'State'], BRIDGE)
        self.assertEqual(out.daily.loc['2026-04-09', 'State'], 'DEFENSIVE')
        self.assertAlmostEqual(out.daily.loc['2026-04-09', 'Weight_QQQ'], .7)
        self.assertEqual(out.daily.Weight_TQQQ.max(), 0)

    def test_already_negative_at_initialization_is_not_fresh_crossunder(self):
        _, sig = april_fixture(); sig.ROC = -1
        self.assertEqual(bridge_states(sig, Config())[0], 'BEAR')
        self.assertNotIn(BRIDGE, bridge_states(sig, Config()))

    def test_ma_breakdown_wins_over_same_bar_roc_crossunder(self):
        _, sig = april_fixture()
        sig.QQQ = [110, 90] + [90] * (len(sig) - 2)
        sig.ROC = [1, -1] + [-1] * (len(sig) - 2)
        states = bridge_states(sig, Config())
        self.assertEqual(states[1], 'BEAR')
        self.assertNotIn(BRIDGE, states)

    def test_new_breakdown_requires_new_later_crossunder(self):
        _, sig = april_fixture()
        sig.loc['2026-04-10':, 'QQQ'] = 90
        states = pd.Series(bridge_states(sig, Config()), index=sig.index)
        self.assertEqual(states.loc['2026-04-10'], 'BEAR')
        self.assertEqual(states.loc['2026-04-13'], 'BEAR')

    def test_exact_zero_followed_by_negative_is_crossunder(self):
        _, sig = april_fixture(); sig.loc['2026-03-31', 'ROC'] = 0
        states = pd.Series(bridge_states(sig, Config()), index=sig.index)
        self.assertEqual(states.loc['2026-04-01'], BRIDGE)

    def test_missing_previous_roc_does_not_invent_a_cross(self):
        _, sig = april_fixture(); sig.loc['2026-03-31', 'ROC'] = np.nan
        sig.loc['2026-03-31', 'ready'] = False
        self.assertNotIn(BRIDGE, bridge_states(sig, Config()))

    def test_two_day_confirmation(self):
        _, sig = april_fixture()
        states = pd.Series(bridge_states(sig, Config(confirm_days=2)), index=sig.index)
        self.assertEqual(states.loc['2026-04-01'], 'BEAR')
        self.assertEqual(states.loc['2026-04-02'], BRIDGE)

    def test_qld_profile_changes_only_aggressive_asset(self):
        data, sig = april_fixture()
        out = execute(data, sig, profile='qld70')
        self.assertAlmostEqual(out.daily.loc['2026-04-02', 'Weight_QQQ'], .7)
        self.assertAlmostEqual(out.daily.loc['2026-04-09', 'Weight_QLD'], .7)
        self.assertEqual(out.daily.Weight_TQQQ.max(), 0)

    def test_bridge_weight_is_configurable_and_no_borrowing(self):
        data, sig = april_fixture()
        out = execute(data, sig, Config(bridge_weight=.5, cost_bps=5))
        self.assertAlmostEqual(out.daily.loc['2026-04-02', 'Weight_QQQ'], .5)
        self.assertTrue((out.daily.filter(like='Value_') >= -1e-8).all().all())
        self.assertAlmostEqual(out.daily.Contributed.iloc[-1], 81500)
        self.assertAlmostEqual(out.daily.Equity.iloc[-1] + sum(t['cost'] for t in out.trades), 81500, places=7)

    def test_existing_rsi_qqq_is_included_in_seventy_percent_not_added(self):
        data, sig = april_fixture(); sig.loc['2026-03-27', 'RSI'] = 15
        out = execute(data, sig)
        self.assertAlmostEqual(out.daily.loc['2026-03-30', 'Weight_QQQ'], .05)
        self.assertAlmostEqual(out.daily.loc['2026-04-02', 'Weight_QQQ'], .70)
        self.assertEqual(out.daily.loc['2026-04-02', 'TacticalValue'], 0)

    def test_no_leveraged_rsi_overlay_during_bridge(self):
        data, sig = april_fixture(); sig.loc['2026-04-06', 'RSI'] = 15
        out = execute(data, sig)
        self.assertTrue(any(e['event'] == 'DIP_SKIPPED_BRIDGE_BASE' for e in out.events))
        self.assertAlmostEqual(out.daily.loc['2026-04-07', 'Weight_QQQ'], .7)
        self.assertEqual(out.daily.loc['2026-04-07', 'Weight_TQQQ'], 0)

    def test_future_changes_do_not_change_past_decisions(self):
        data, sig = april_fixture()
        before = execute(data, sig)
        sig.loc['2026-04-08':, 'ROC'] = 5
        after = execute(data, sig)
        pd.testing.assert_frame_equal(before.daily.loc[:'2026-04-08'], after.daily.loc[:'2026-04-08'])

    def test_pending_last_crossunder_not_executed(self):
        data, sig = april_fixture(); sig.QQQ = 90; sig.ROC = 1
        sig.iloc[-1, sig.columns.get_loc('ROC')] = -1
        out = execute(data, sig)
        self.assertFalse(any(t['asset'] == 'QQQ' for t in out.trades))

    def test_legacy_signal_reuse_rejected_for_new_strategy(self):
        data, sig = april_fixture(); data.signal_prices = None
        with self.assertRaisesRegex(ValueError, 'separate signal prices'):
            execute(data, sig)

    def test_invalid_bridge_weights_rejected(self):
        for value in (-.1, 0, 1.01, float('nan'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Config(bridge_weight=value)


class SignalSeparationTests(unittest.TestCase):
    def test_roc_formula_matches_screenshot_rounding_on_arithmetic_fixture(self):
        # Endpoint values are a formula example; all intermediate bars are invented.
        index = sessions('2025-01-01', '2026-04-15')
        data = data_for(index)
        data.signal_prices.QQQ = 600.; data.signal_prices.VTV = 200.
        data.signal_prices.loc['2026-02-10'] = [611.47, 205.83]
        data.signal_prices.loc['2026-04-01'] = [584.31, 196.67]
        data.signal_prices.loc['2026-03-31'] = [590., 205.]
        report = tradingview_reference_check(data, Config())
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['actual']['ROCReferenceDate'], '2026-02-10')
        self.assertAlmostEqual(report['actual']['ROC35_pct'], -.008915383135310595, places=8)
        data.signal_prices.loc['2026-04-01', 'VTV'] = 198
        self.assertEqual(tradingview_reference_check(data, Config())['status'], 'mismatch')

    def test_signal_price_ex_dividend_drop_does_not_double_count_pnl(self):
        index = sessions('2025-01-01', '2025-03-01')
        data = data_for(index)
        data.signal_prices.loc[index[20]:, 'QQQ'] = 99.
        out = run(data, Config(monthly=0, cost_bps=0, ma_period=5, roc_period=3, rsi_period=3),
                  'hold_qqq', 'tqqq70', 'long_history')
        self.assertTrue(np.allclose(out.daily.UnitNAV, 1))
        self.assertEqual(out.daily.Contributed.iloc[-1], 80000)
        self.assertLess(indicators(data, Config(ma_period=5)).QQQ.iloc[-1], data.prices.QQQ.iloc[-1])

    def test_attach_changes_signals_not_total_returns(self):
        index, frames, _ = quotes_fixture()
        data = data_for(index); data.observed_start['VTV'] = index[20]
        before = data.returns.copy(deep=True)
        out = attach_quotes(data, frames, {'source': 'TEST_ONLY'})
        pd.testing.assert_frame_equal(out.returns, before)
        pd.testing.assert_frame_equal(data.returns, before)
        self.assertEqual(out.signal_basis, BASIS)
        self.assertTrue(out.signal_synthetic.VTV.iloc[:21].all())
        self.assertFalse(out.signal_synthetic.VTV.iloc[21:].any())
        ix = index[55:]
        direct = (frames['VTV'].Close / frames['QQQ'].Close).pct_change(35, fill_method=None) * 100
        np.testing.assert_allclose(indicators(out, Config()).ROC.loc[ix], direct.loc[ix], atol=1e-10)

    def test_missing_observed_signal_bar_rejected(self):
        index, frames, _ = quotes_fixture()
        data = data_for(index); data.observed_start['VTV'] = index[20]
        frames['VTV'] = frames['VTV'].drop(index[50])
        with self.assertRaisesRegex(ValueError, 'missing/extra'):
            attach_quotes(data, frames, {})

    def test_lost_first_observed_vtv_bar_not_treated_as_preinception(self):
        index, frames, _ = quotes_fixture()
        data = data_for(index); data.observed_start['VTV'] = index[20]
        frames['VTV'] = frames['VTV'].iloc[1:]
        with self.assertRaisesRegex(ValueError, 'start differs'):
            attach_quotes(data, frames, {})

    def test_signal_prefix_unchanged_by_future_quotes(self):
        index, frames, _ = quotes_fixture()
        data = data_for(index); data.observed_start['VTV'] = index[20]
        before = indicators(attach_quotes(data, frames, {}), Config())
        frames['QQQ'].loc[index[300]:, 'Close'] *= 1.3
        after = indicators(attach_quotes(data, frames, {}), Config())
        pd.testing.assert_frame_equal(before.iloc[:300], after.iloc[:300])


class SignalSnapshotTests(unittest.TestCase):
    def build(self, root, frames=None, metadata=None):
        _, source, meta = quotes_fixture()
        source, meta = frames or source, metadata or meta
        with contextlib.redirect_stdout(io.StringIO()):
            return prepare_snapshot('2024-01-01', '2026-01-01', root,
                                    fetcher=lambda n, *_: (source[n], meta[n]))

    def test_roundtrip_keeps_both_close_and_adjusted_close(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.build(root)
            frames, provenance = read_snapshot(root, end='2026-01-01')
            self.assertEqual(provenance['manifest']['basis'], BASIS)
            self.assertFalse(provenance['manifest']['history_options']['auto_adjust'])
            self.assertTrue((frames['QQQ'].Close != frames['QQQ']['Adj Close']).all())
            self.assertFalse(provenance['manifest']['tradingview_feed'])

    def test_changed_file_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run_dir = self.build(root)
            with (run_dir / 'prices/QQQ.csv').open('a') as stream:
                stream.write('\n')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                read_snapshot(root, end='2026-01-01')

    def test_failed_refresh_blocks_previous_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); prior = self.build(root)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                prepare_snapshot('2024-01-01', '2026-01-01', root,
                    fetcher=lambda *_: (_ for _ in ()).throw(RuntimeError('network failed')))
            self.assertTrue((prior / 'prices/QQQ.csv').exists())
            with self.assertRaisesRegex(ValueError, 'failed/incomplete'):
                read_snapshot(root, end='2026-01-01')

    def test_stale_cutoff_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.build(root)
            with self.assertRaisesRegex(ValueError, 'date mismatch'):
                read_snapshot(root, end='2026-01-02')

    def test_active_lock_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.build(root); (root / '.download.lock').touch()
            with self.assertRaisesRegex(ValueError, 'being prepared'):
                read_snapshot(root, end='2026-01-01')

    def test_cli_new_strategy_without_quotes_fails_closed(self):
        index, _, _ = quotes_fixture()
        data = data_for(index); data.signal_prices = None
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'load_legacy', return_value=data), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['--accept-legacy-adjusted', '--strategies', 'bear_roc_bridge', '--output', temp])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(next(Path(temp).glob('*/manifest.json')).read_text())['status'], 'failed')

    def test_cli_separate_quotes_outputs_and_provenance(self):
        index, _, _ = quotes_fixture()
        data = data_for(index); data.signal_prices = None; data.observed_start['VTV'] = index[20]
        data.audit['effective_end_exclusive'] = '2026-01-01'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.build(root / 'signals')
            with patch.object(cli, 'load_legacy', return_value=data), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(['--accept-legacy-adjusted', '--signal-source', 'split_close',
                    '--signal-dir', str(root / 'signals'), '--strategies', 'bear_roc_bridge',
                    '--profiles', 'qld70', '--windows', 'long_history', '--output', str(root / 'results')])
            self.assertEqual(code, 0)
            run_dir = next((root / 'results').iterdir())
            with contextlib.redirect_stdout(io.StringIO()):
                cli.verify_run(run_dir)
            self.assertTrue((run_dir / 'signal_comparison.csv').exists())
            self.assertEqual(json.loads((run_dir / 'manifest.json').read_text())['signal_basis'], BASIS)
            self.assertEqual(json.loads((run_dir / 'tradingview_reference_check.json').read_text())['status'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()
