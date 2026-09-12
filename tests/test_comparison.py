"""Synthetic fixtures ONLY. Never market-performance claims or source certification."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtest.config import Config, PROFILES, STRATEGIES
from backtest.data import Dataset, indicators, join_history, load_legacy, rate_inputs, sessions
from backtest.engine import base_weights, run, signal_states
from backtest.report import summarize, xirr, dd_details, comparison_pairs
import run_comparison as cli


def fixture(n=100, r=0.):
    index = sessions('2020-01-01', '2022-01-01')[:n]
    names = ['QQQ', 'VTV', 'TQQQ', 'QLD', 'SGOV']
    rates = pd.DataFrame(r, index=index, columns=names)
    rates.SGOV = 0.
    rates.iloc[0] = np.nan
    prices = (1 + rates.fillna(0)).cumprod() * 100
    sources = pd.DataFrame({n: 'observed:' + n for n in names}, index=index)
    return Dataset(prices, rates, sources, pd.DataFrame(False, index=index, columns=names),
                   {n: index[0] for n in names}, {'source_mode': 'TEST_NOT_MARKET'})


def signals(data, bull=True, roc=-1., rsi=40.):
    return pd.DataFrame({'QQQ': 110. if bull else 90., 'MA': 100., 'RSI': rsi,
                         'ROC': roc, 'ready': True}, index=data.prices.index)


def execute(data, cfg=None, strategy='ma_roc', profile='tqqq70', signal=None, **kwargs):
    cfg = cfg or Config(monthly=0, cost_bps=0)
    signal = signals(data) if signal is None else signal
    with patch('backtest.engine.indicators', return_value=signal):
        return run(data, cfg, strategy, profile, 'long_history', **kwargs)


def raw_fixture(root):
    idx = sessions('1999-03-10', '2012-01-01')
    starts = {'QQQ': 0, 'VIVAX': 0, 'SPY': 0, 'VTV': 50, 'QLD': 100,
              'TQQQ': 200, 'SGOV': 250, 'BIL': 120, 'IRX': 0}
    for j, (name, start) in enumerate(starts.items()):
        index = idx[start:]
        value = (5. + np.sin(np.arange(len(index)) / 50) if name == 'IRX' else
                 100 * np.exp(np.arange(len(index)) * (.00005 + .00001 * j) + .01 * np.sin(np.arange(len(index)) / 8)))
        pd.DataFrame({'Close': value, 'Open': value, 'High': value, 'Low': value, 'Volume': 1000}, index=index).to_csv(root / f'{name}_raw.csv', index_label='Date')
    return idx


class EngineTests(unittest.TestCase):
    def test_capital_and_external_flows_are_not_returns(self):
        data = fixture(70)
        out = execute(data, Config(cost_bps=0), strategy='hold_qqq')
        self.assertTrue(np.allclose(out.daily.UnitNAV, 1))
        self.assertAlmostEqual(out.daily.Equity.iloc[-1], out.daily.Contributed.iloc[-1])
        self.assertAlmostEqual(summarize(out)['twr_cagr'], 0)
        self.assertAlmostEqual(summarize(out)['money_weighted_xirr'], 0, places=8)

    def test_monthly_deposit_first_session_after_initial_month(self):
        out = execute(fixture(70), Config(cost_bps=0))
        dates = [x['execution_date'] for x in out.events if x['event'] == 'MONTHLY_CONTRIBUTION']
        self.assertEqual(dates, ['2020-02-03', '2020-03-02', '2020-04-01'])
        self.assertEqual(out.daily.Contributed.iloc[-1], 84500)

    def test_initial_cost_self_financed_and_weight_exact(self):
        cfg = Config(monthly=0, cost_bps=100)
        out = execute(fixture(3), cfg, strategy='hold_qqq')
        self.assertAlmostEqual(out.daily.Equity.iloc[-1], cfg.initial / 1.01, places=7)
        self.assertAlmostEqual(out.daily.Weight_QQQ.iloc[-1], .7)
        self.assertAlmostEqual(sum(x['cost'] for x in out.trades), cfg.initial - out.daily.Equity.iloc[-1], places=7)

    def test_turnover_fee_matches_ledger(self):
        data = fixture(30)
        sig = signals(data)
        sig.loc[sig.index[10:20], 'QQQ'] = 90
        out = execute(data, Config(monthly=0, cost_bps=10), signal=sig)
        self.assertAlmostEqual(sum(x['cost'] for x in out.trades), 80000 - out.daily.Equity.iloc[-1], places=6)
        self.assertTrue((out.daily.filter(like='Value_') >= -1e-8).all().all())

    def test_previous_signal_next_close_not_same_close_return(self):
        data = fixture(6)
        data.returns.TQQQ = [np.nan, 0., -.5, 0., 0., 0.]
        sig = signals(data, bull=False)
        sig.loc[sig.index[2:], 'QQQ'] = 110
        out = execute(data, signal=sig)
        buys = [x for x in out.trades if x['asset'] == 'TQQQ' and x['side'] == 'BUY']
        self.assertEqual(buys[0]['signal_date'], str(sig.index[2].date()))
        self.assertEqual(buys[0]['execution_date'], str(sig.index[3].date()))
        self.assertAlmostEqual(out.daily.Equity.iloc[-1], 80000)

    def test_two_session_execution_lag(self):
        out = execute(fixture(6), Config(monthly=0, cost_bps=0, signal_lag=2))
        for t in out.trades:
            dates = out.daily.index
            self.assertEqual(dates.get_loc(t['execution_date']) - dates.get_loc(t['signal_date']), 2)

    def test_final_signal_not_executed_past_end(self):
        data = fixture(6)
        sig = signals(data, bull=False)
        sig.loc[sig.index[-1], 'QQQ'] = 110
        out = execute(data, signal=sig)
        self.assertFalse(any(x['asset'] == 'TQQQ' for x in out.trades))

    def test_future_changes_do_not_change_prior_results(self):
        data = fixture(300, .001)
        cfg = Config(monthly=1500, cost_bps=5)
        prefix = run(data, cfg, 'bull_bear_rsi', 'tqqq70', 'long_history')
        changed = fixture(300, .001)
        changed.prices.loc[changed.prices.index[250]:, 'QQQ'] *= 1.3
        changed.returns.loc[changed.returns.index[250]:, 'TQQQ'] = -.01
        after = run(changed, cfg, 'bull_bear_rsi', 'tqqq70', 'long_history')
        pd.testing.assert_frame_equal(prefix.daily.iloc[:250], after.daily.iloc[:250])

    def test_warmup_is_cash_without_invented_ma(self):
        data = fixture(230, .001)
        out = run(data, Config(monthly=0, cost_bps=0), 'ma200', 'tqqq70', 'long_history')
        self.assertTrue(out.daily.State.iloc[:200].eq('WAIT').all())
        self.assertEqual(out.daily.State.iloc[200], 'AGGRESSIVE')
        self.assertTrue(out.daily.UnitNAV.iloc[:200].eq(1).all())

    def test_all_profiles_have_no_borrowing_and_correct_exposure(self):
        for p, exposure in [('tqqq70', 2.1), ('qld70', 1.4), ('qld100', 2.), ('tqqq46', 1.4)]:
            with self.subTest(profile=p):
                out = execute(fixture(6), profile=p)
                self.assertAlmostEqual(out.daily.ApproxDailyExposure.iloc[-1], exposure)
                self.assertAlmostEqual(out.daily.filter(like='Weight_').sum(axis=1).iloc[-1], 1)

    def test_ma_and_roc_sign_state_table(self):
        data = fixture(5)
        sig = signals(data)
        sig.loc[sig.index[1:3], 'ROC'] = 1.
        sig.loc[sig.index[3:], 'QQQ'] = 90
        self.assertEqual(list(signal_states(sig, Config(), 'ma_roc')),
                         ['AGGRESSIVE', 'DEFENSIVE', 'DEFENSIVE', 'BEAR', 'BEAR'])

    def test_zero_roc_preserves_prior_tier(self):
        sig = signals(fixture(5))
        sig.ROC = [-1, 0, 1, 0, -1]
        self.assertEqual(list(signal_states(sig, Config(), 'ma_roc')),
                         ['AGGRESSIVE', 'AGGRESSIVE', 'DEFENSIVE', 'DEFENSIVE', 'AGGRESSIVE'])

    def test_confirmation_avoids_single_day_state_change(self):
        sig = signals(fixture(5))
        sig.ROC = [-1, -1, 1, -1, -1]
        self.assertEqual(list(signal_states(sig, Config(confirm_days=2), 'ma_roc')),
                         ['WAIT', 'AGGRESSIVE', 'AGGRESSIVE', 'AGGRESSIVE', 'AGGRESSIVE'])

    def test_single_oversold_excursion_not_daily_buy(self):
        data = fixture(15)
        sig = signals(data)
        sig.loc[sig.index[3:10], 'RSI'] = 15.
        out = execute(data, strategy='bull_rsi', signal=sig)
        buys = [x for x in out.events if x['event'] == 'BULL_DIP_BUY']
        self.assertEqual(len(buys), 1)
        self.assertAlmostEqual(buys[0]['filled'], 4000)

    def test_no_rearm_until_reset_threshold(self):
        data = fixture(10)
        sig = signals(data)
        sig.RSI = [40, 40, 15, 25, 15, 25, 15, 25, 15, 25]
        out = execute(data, strategy='bull_rsi', signal=sig)
        self.assertEqual(sum(x['event'] == 'BULL_DIP_BUY' for x in out.events), 1)

    def test_tactical_cap_and_expiry(self):
        data = fixture(25)
        sig = signals(data)
        sig.RSI = [40, 40, 15, 40, 15, 40, 15] + [40] * 18
        out = execute(data, strategy='bull_bear_rsi', signal=sig)
        self.assertLessEqual(out.daily.TacticalValue.max(), 8000.00001)
        self.assertEqual(sum(x['event'] == 'BULL_DIP_BUY' for x in out.events), 2)
        self.assertEqual(sum(x['event'] == 'TACTICAL_TIME_EXIT' for x in out.events), 2)
        self.assertAlmostEqual(out.daily.TacticalValue.iloc[-1], 0)

    def test_rsi_exit_is_logged_and_lagged(self):
        data = fixture(10)
        sig = signals(data)
        sig.loc[sig.index[3], 'RSI'] = 15
        sig.loc[sig.index[5:], 'RSI'] = 55
        out = execute(data, strategy='bull_rsi', signal=sig)
        exit_event = next(x for x in out.events if x['event'] == 'TACTICAL_RSI_EXIT')
        self.assertEqual(exit_event['execution_date'], str(sig.index[6].date()))

    def test_bear_entry_buys_qqq_not_leveraged(self):
        data = fixture(12)
        sig = signals(data, bull=False)
        sig.loc[sig.index[4], 'RSI'] = 15
        out = execute(data, strategy='bull_bear_rsi', signal=sig)
        dip = next(x for x in out.events if x['event'] == 'BEAR_DIP_BUY')
        self.assertEqual(dip['asset'], 'QQQ')
        self.assertAlmostEqual(dip['filled'], 4000)

    def test_transition_day_exit_priority_no_dip(self):
        data = fixture(12)
        sig = signals(data)
        sig.loc[sig.index[4:], 'QQQ'] = 90
        sig.loc[sig.index[4], 'RSI'] = 15
        out = execute(data, strategy='bull_bear_rsi', signal=sig)
        self.assertTrue(any(x['event'] == 'DIP_SKIPPED_STATE_CHANGE' for x in out.events))
        self.assertFalse(any(x['event'] == 'BEAR_DIP_BUY' for x in out.events))

    def test_roc_disables_leveraged_dip_only_in_improved_version(self):
        data = fixture(12)
        sig = signals(data, roc=1)
        sig.loc[sig.index[4], 'RSI'] = 15
        improved = execute(data, strategy='bull_rsi', signal=sig)
        original = execute(data, strategy='user_rules', signal=sig)
        self.assertEqual(next(x['asset'] for x in improved.events if x['event'] == 'BULL_DIP_BUY'), 'QQQ')
        self.assertEqual(next(x['asset'] for x in original.events if x['event'] == 'BULL_DIP_BUY'), 'TQQQ')
        self.assertAlmostEqual(next(x['filled'] for x in original.events if x['event'] == 'BULL_DIP_BUY'), 1200)

    def test_original_initial_aggressive_even_in_bear(self):
        data = fixture(6)
        out = execute(data, strategy='user_rules', signal=signals(data, bull=False))
        self.assertEqual(out.daily.State.iloc[1], 'AGGRESSIVE')
        self.assertEqual(out.daily.State.iloc[2], 'BEAR')

    def test_original_no_time_exit(self):
        data = fixture(25)
        sig = signals(data, bull=False)
        sig.loc[sig.index[4], 'RSI'] = 15
        out = execute(data, strategy='user_rules', signal=sig)
        self.assertGreater(out.daily.TacticalValue.iloc[-1], 0)
        self.assertFalse(any(x['event'] == 'TACTICAL_TIME_EXIT' for x in out.events))

    def test_qld100_cannot_buy_dip_without_cash(self):
        data = fixture(12)
        sig = signals(data)
        sig.loc[sig.index[4], 'RSI'] = 15
        out = execute(data, strategy='bull_rsi', profile='qld100', signal=sig)
        self.assertTrue(any(x['event'] == 'DIP_SKIPPED_NO_CASH_OR_CAP' for x in out.events))

    def test_tactical_sleeve_survives_monthly_rebalance(self):
        data = fixture(30)
        sig = signals(data)
        sig.loc[pd.Timestamp('2020-01-29'), 'RSI'] = 15
        out = execute(data, Config(cost_bps=0), strategy='bull_rsi', signal=sig)
        self.assertEqual(out.daily.loc['2020-02-03', 'TacticalValue'], 4000)
        self.assertAlmostEqual(out.daily.loc['2020-02-03', 'Value_TQQQ'], .7 * 81500 + 4000)

    def test_state_only_uses_deposits_without_sales(self):
        out = execute(fixture(60, .001), Config(cost_bps=5, rebalance='state_only'), strategy='hold_leveraged')
        self.assertFalse(any(x['side'] == 'SELL' for x in out.trades))
        self.assertEqual(out.daily.Value_USD.iloc[-1], 0)

    def test_unavailable_held_return_fails(self):
        data = fixture(10)
        data.returns.loc[data.returns.index[5], 'TQQQ'] = np.nan
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            execute(data)

    def test_all_observed_rejects_held_synthetic_returns(self):
        data = fixture(230, .001)
        data.synthetic.loc[data.prices.index[205], 'SGOV'] = True
        with self.assertRaisesRegex(ValueError, 'synthetic'):
            run(data, Config(), 'hold_leveraged', 'tqqq70', 'all_observed')

    def test_costs_and_cashflows_reconcile_for_tactical_trades(self):
        data = fixture(60)
        sig = signals(data)
        sig.loc[sig.index[[4, 17, 28]], 'RSI'] = 15
        out = execute(data, Config(cost_bps=10), strategy='bull_bear_rsi', signal=sig)
        self.assertAlmostEqual(out.daily.Contributed.iloc[-1] - out.daily.Equity.iloc[-1],
                               sum(x['cost'] for x in out.trades), places=5)

    def test_xirr_known_one_year_return(self):
        self.assertAlmostEqual(xirr([(pd.Timestamp('2021-01-01'), -100)], pd.Timestamp('2022-01-01'), 110),
                               1.1 ** (365.2425 / 365) - 1, places=8)

    def test_drawdown_recovery_and_initial_fees(self):
        idx = pd.date_range('2020-01-01', periods=5)
        info = dd_details(pd.Series([.99, 1.2, .6, .9, 1.21], index=idx))
        self.assertAlmostEqual(info['max_drawdown'], -.5)
        self.assertEqual(info['drawdown_recovered_date'], '2020-01-05')
        self.assertEqual(info['longest_underwater_calendar_days'], 2)

    def test_bad_configuration_rejected(self):
        for args in [{'cost_bps': -1}, {'initial': 0}, {'signal_lag': 0}, {'dip_cap': .01}, {'monthly': float('nan')}]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                Config(**args)


class DataTests(unittest.TestCase):
    def test_legacy_acknowledgement_required(self):
        with self.assertRaisesRegex(ValueError, 'Legacy|legacy'):
            load_legacy(Path('/no/such/path'), Config())

    def test_joint_history_and_observed_returns_preserved(self):
        data = fixture(20, .001)
        idx = data.prices.index
        observed = data.prices.TQQQ.iloc[10:]
        out, returns, src, syn = join_history('TQQQ', observed, idx, pd.Series(.003, index=idx), 'model')
        np.testing.assert_allclose(returns.iloc[11:], .001)
        self.assertTrue(syn.iloc[:11].all()); self.assertFalse(syn.iloc[11:].any())
        self.assertTrue(src.iloc[10].startswith('transition:'))
        out2, *_ = join_history('TQQQ', observed * 10000, idx, pd.Series(.003, index=idx), 'model')
        np.testing.assert_allclose(out, out2)

    def test_model_leading_unknown_rates_not_filled_with_zero(self):
        data = fixture(20)
        model = pd.Series(.003, index=data.prices.index)
        model.iloc[:4] = np.nan
        out, returns, *_ = join_history('TQQQ', data.prices.TQQQ.iloc[10:], data.prices.index, model, 'model')
        self.assertTrue(out.iloc[:3].isna().all())
        self.assertEqual(out.iloc[3], 100)
        self.assertTrue(returns.iloc[:4].isna().all())

    def test_gap_or_ruin_in_model_fails(self):
        data = fixture(20)
        for v in [np.nan, -1, -1.01]:
            m = pd.Series(.003, index=data.prices.index); m.iloc[5] = v
            with self.subTest(v=v), self.assertRaises(ValueError):
                join_history('TQQQ', data.prices.TQQQ.iloc[10:], data.prices.index, m, 'model')

    def test_rates_lag_and_weekend_calendar_days(self):
        idx = sessions('2020-01-03', '2020-01-08')
        rate = pd.Series(5., index=pd.date_range('2019-12-01', '2020-02-01'))
        r = rate_inputs(rate, idx, Config())
        self.assertEqual(r.loc['2020-01-06', 'calendar_days'], 3)
        self.assertLessEqual(r.loc['2020-01-06', 'observation_date'], pd.Timestamp('2020-01-01'))

    def test_legacy_end_to_end_and_missing_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); idx = raw_fixture(root)
            data = load_legacy(root, Config(), acknowledge=True)
            self.assertEqual(data.prices.index[0], idx[0])
            self.assertTrue(data.audit['adjustment_assumption_accepted'])
            self.assertFalse(data.audit['independently_verified'])
            bad = pd.read_csv(root / 'TQQQ_raw.csv').drop(20)
            bad.to_csv(root / 'TQQQ_raw.csv', index=False)
            with self.assertRaisesRegex(ValueError, 'missing/extra'):
                load_legacy(root, Config(), acknowledge=True)

    def test_explicit_end_does_not_silently_trim_trailing_gaps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw_fixture(root)
            bad = pd.read_csv(root / 'SGOV_raw.csv').iloc[:-2]
            bad.to_csv(root / 'SGOV_raw.csv', index=False)
            with self.assertRaisesRegex(ValueError, 'missing/extra'):
                load_legacy(root, Config(), end='2012-01-01', acknowledge=True)

    def test_financing_sensitivity_changes_only_preinception(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw_fixture(root)
            a = load_legacy(root, Config(), acknowledge=True)
            b = load_legacy(root, Config(funding_spread=.015), acknowledge=True)
            mask = ~a.synthetic.TQQQ
            np.testing.assert_allclose(a.returns.loc[mask, 'TQQQ'], b.returns.loc[mask, 'TQQQ'])
            self.assertTrue((b.returns.TQQQ.iloc[10:100] < a.returns.TQQQ.iloc[10:100]).all())

    def test_cli_outputs_and_checksum_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw_fixture(root)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(['--accept-legacy-adjusted', '--raw-dir', str(root), '--output', str(root / 'results'),
                                 '--windows', 'post_tqqq', '--strategies', 'ma_roc', '--profiles', 'qld70'])
            self.assertEqual(code, 0)
            output = next((root / 'results').iterdir())
            with contextlib.redirect_stdout(io.StringIO()):
                cli.verify_run(output)
            self.assertTrue((output / 'report.html').exists())
            summary = pd.read_csv(output / 'summary.csv')
            self.assertEqual(len(summary), 1)
            self.assertGreater(summary.iloc[0].final_equity, 0)
            with (output / 'summary.csv').open('a') as f:
                f.write('\nchanged')
            with self.assertRaisesRegex(ValueError, 'Checksum'):
                cli.verify_run(output)

    def test_cli_failed_run_never_passed(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['--output', temp, '--raw-dir', temp])
            self.assertEqual(code, 1)
            manifest = json.loads(next(Path(temp).glob('*/manifest.json')).read_text())
            self.assertEqual(manifest['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
