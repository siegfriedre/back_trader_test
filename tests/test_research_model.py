"""Invented offline fixtures only: these are not market data."""
import unittest

import numpy as np
import pandas as pd

import prepare_research as prep
from research_model import (Assumptions, ResearchDataError, add_indicators,
                            cash_returns, checked_close, lagged_rate,
                            leveraged_returns, make_histories, proxy_chain,
                            splice_returns, valid_index, wilder_rsi)


class ResearchModelTests(unittest.TestCase):
    def setUp(self):
        self.idx = pd.bdate_range("1999-03-10", periods=350)
        self.base = pd.Series(100 * np.exp(np.arange(350) * .0002 + np.sin(np.arange(350)) * .005), index=self.idx)
        self.rate = pd.Series(.05, index=pd.date_range("1999-02-01", self.idx[-1]))
        self.a = Assumptions()

    def inputs(self):
        names = ["QQQ", "SPY", "QLD", "TQQQ", "SGOV", "VOO", "VTV", "SCHD", "CGDV", "KO", "VIVAX", "VYM", "BIL", "VIX"]
        data = {}
        for j, name in enumerate(names):
            x = 100 * np.exp(np.arange(350) * (.0001 + .00003 * j) + np.sin(np.arange(350) / (j + 1)) * .003)
            start = 0 if name in {"QQQ", "SPY", "KO", "VIVAX", "VIX"} else 100 + j
            data[name] = pd.Series(x, index=self.idx).iloc[start:]
        return data

    def test_bad_indices_rejected(self):
        for idx in [self.idx[::-1], self.idx.insert(0, self.idx[0]), self.idx.tz_localize("UTC"), self.idx + pd.Timedelta(hours=1)]:
            with self.subTest(index=idx[:2]), self.assertRaises(ResearchDataError):
                valid_index(idx)

    def test_post_listing_missing_prices_never_filled(self):
        for pos in [125, -1]:
            with self.subTest(position=pos), self.assertRaises(ResearchDataError):
                checked_close(self.base.iloc[100:].drop(self.base.index[pos]), self.idx, "target")

    def test_nonfinite_or_nonpositive_prices_rejected(self):
        for value in [np.nan, np.inf, 0, -1]:
            bad = self.base.copy()
            bad.iloc[20] = value
            with self.subTest(value=value), self.assertRaises(ResearchDataError):
                checked_close(bad, self.idx, "target")

    def test_explicit_pre_listing_model_required(self):
        with self.assertRaises(ResearchDataError):
            splice_returns("target", self.base.iloc[100:], self.idx)

    def test_splice_transition_and_real_returns(self):
        proxy = pd.Series(.001, index=self.idx)
        source = pd.Series("test_proxy", index=self.idx)
        out = splice_returns("target", self.base.iloc[100:], self.idx, proxy, source)
        self.assertTrue(out.IsSyntheticReturn.iloc[:101].all())
        self.assertFalse(out.IsSyntheticReturn.iloc[101:].any())
        self.assertEqual(out.ReturnSource.iloc[100], "transition:test_proxy")
        self.assertTrue(out.HasObservedClose.iloc[100])
        np.testing.assert_allclose(out.DailyReturn.iloc[101:], self.base.pct_change(fill_method=None).iloc[101:])
        pd.testing.assert_series_equal(out.ObservedAdjustedClose.iloc[100:], self.base.iloc[100:], check_names=False)

    def test_first_return_not_invented_zero(self):
        out = splice_returns("observed", self.base, self.idx)
        self.assertTrue(pd.isna(out.DailyReturn.iloc[0]))
        self.assertEqual(out.ResearchClose.iloc[0], 100)

    def test_no_fabricated_ohlcv(self):
        out = splice_returns("observed", self.base, self.idx)
        self.assertFalse({"Open", "High", "Low", "Volume"}.intersection(out.columns))

    def test_future_listing_price_is_not_an_anchor(self):
        proxy = pd.Series(.001, index=self.idx)
        source = pd.Series("test_proxy", index=self.idx)
        a = splice_returns("target", self.base.iloc[100:], self.idx, proxy, source)
        b = splice_returns("target", self.base.iloc[100:] * 1000, self.idx, proxy, source)
        np.testing.assert_allclose(a.ResearchClose, b.ResearchClose)

    def test_future_observations_do_not_change_earlier_history(self):
        changed = self.base.copy()
        changed.iloc[200:] *= 2
        a = splice_returns("target", self.base, self.idx)
        b = splice_returns("target", changed, self.idx)
        pd.testing.assert_frame_equal(a.iloc[:200], b.iloc[:200])

    def test_model_ruin_is_not_clipped_or_resurrected(self):
        r = pd.Series(.001, index=self.idx)
        r.iloc[50] = -1.01
        with self.assertRaisesRegex(ResearchDataError, "ruin"):
            splice_returns("target", self.base.iloc[100:], self.idx, r, pd.Series("model", index=self.idx))

    def test_proxy_switch_needs_two_observed_closes(self):
        r, src = proxy_chain(self.idx, [("long", self.base), ("short", self.base.iloc[100:] * 2)])
        self.assertEqual(src.iloc[100], "long")
        self.assertEqual(src.iloc[101], "short")
        self.assertTrue(pd.isna(r.iloc[0]))

    def test_bad_candidate_not_hidden_by_good_proxy(self):
        with self.assertRaises(ResearchDataError):
            proxy_chain(self.idx, [("good", self.base), ("bad", self.base.iloc[100:].drop(self.idx[200]))])

    def test_daily_compounding_not_multiple_of_total_return(self):
        idx = pd.date_range("2020-01-01", periods=3)
        r = pd.Series([np.nan, .1, -.1], index=idx)
        rates = pd.DataFrame({"AnnualRate": [np.nan, 0, 0], "CalendarDays": [np.nan, 1, 1]}, index=idx)
        out = leveraged_returns(r, rates, 3, Assumptions(annual_fund_fee=0, annual_funding_spread=0))
        self.assertAlmostEqual(float((1 + out.iloc[1:]).prod() - 1), -.09)
        self.assertNotAlmostEqual(float((1 + out.iloc[1:]).prod() - 1), 3 * ((1.1 * .9) - 1))

    def test_previous_available_rates_not_current_or_future(self):
        inputs = lagged_rate(self.rate, self.idx, self.a)
        previous = pd.Series(self.idx[:-1], index=self.idx[1:])
        self.assertTrue((inputs.RateObservationDate.iloc[1:] <= previous - pd.Timedelta(days=2)).all())
        changed = self.rate.copy()
        changed.loc[changed.index >= self.idx[200]] = .15
        newer = lagged_rate(changed, self.idx, self.a)
        pd.testing.assert_frame_equal(inputs.iloc[:201], newer.iloc[:201])

    def test_friday_to_monday_accrues_three_days(self):
        inputs = lagged_rate(self.rate, self.idx, self.a)
        monday = next(i for i in range(1, len(self.idx)) if self.idx[i].dayofweek == 0)
        self.assertEqual(inputs.CalendarDays.iloc[monday], 3)

    def test_unknown_initial_rate_is_not_filled_with_zero(self):
        with self.assertRaisesRegex(ResearchDataError, "No previously"):
            lagged_rate(self.rate.loc[self.idx[50]:], self.idx, self.a)

    def test_stale_rates_rejected(self):
        with self.assertRaisesRegex(ResearchDataError, "Stale"):
            lagged_rate(self.rate.iloc[:5], self.idx, self.a)

    def test_percentage_instead_of_fraction_rejected(self):
        with self.assertRaisesRegex(ResearchDataError, "decimal fractions"):
            lagged_rate(self.rate * 100, self.idx, self.a)

    def test_zero_yield_allowed_and_cash_can_lose_fees(self):
        inputs = lagged_rate(self.rate * 0, self.idx, self.a)
        self.assertTrue((cash_returns(inputs, self.a).iloc[1:] < 0).all())

    def test_discount_quote_converted_not_used_as_price(self):
        inputs = pd.DataFrame({"AnnualRate": [.05], "CalendarDays": [3]})
        result = cash_returns(inputs, Assumptions(annual_cash_fee=0))
        self.assertAlmostEqual(result.iloc[0], .05 * 3 / (360 - .05 * 91))

    def test_assumptions_validated(self):
        for kw in [{"annual_fund_fee": -1}, {"annual_funding_spread": np.nan}, {"rate_publication_lag_days": 0}, {"style_proxy": "future_best_performer"}]:
            with self.subTest(kwargs=kw), self.assertRaises(ResearchDataError):
                Assumptions(**kw)

    def test_wilder_rsi_and_warmup(self):
        actual = wilder_rsi(pd.Series([10., 11., 10., 12., 11.]), 3)
        self.assertAlmostEqual(actual.iloc[3], 75)
        self.assertAlmostEqual(actual.iloc[4], 54.54545454545)
        frame = add_indicators(splice_returns("base", self.base, self.idx))
        self.assertEqual(len(frame), 350)
        self.assertFalse(frame.IndicatorsReady.iloc[:199].any())
        self.assertTrue(frame.IndicatorsReady.iloc[199:].all())

    def test_indicator_prefix_invariance(self):
        full = add_indicators(splice_returns("base", self.base, self.idx))
        prefix = add_indicators(splice_returns("base", self.base.iloc[:250], self.idx[:250]))
        pd.testing.assert_frame_equal(full.iloc[:250], prefix)

    def test_financing_stress_affects_only_modeled_returns(self):
        inputs = self.inputs()
        base, _ = make_histories(inputs, self.idx, self.rate, self.rate, self.a)
        stress, _ = make_histories(inputs, self.idx, self.rate, self.rate, Assumptions(annual_funding_spread=.015))
        mask = base["TQQQ"].IsSyntheticReturn & base["TQQQ"].DailyReturn.notna()
        self.assertTrue((stress["TQQQ"].loc[mask, "DailyReturn"] < base["TQQQ"].loc[mask, "DailyReturn"]).all())
        np.testing.assert_allclose(stress["TQQQ"].loc[~mask, "DailyReturn"], base["TQQQ"].loc[~mask, "DailyReturn"], equal_nan=True)

    def test_style_scenario_does_not_change_real_returns(self):
        inputs = self.inputs()
        base, _ = make_histories(inputs, self.idx, self.rate, self.rate, self.a)
        alternative, _ = make_histories(inputs, self.idx, self.rate, self.rate, Assumptions(style_proxy="broad_equity"))
        for name in ["SCHD", "CGDV"]:
            mask = ~base[name].IsSyntheticReturn
            np.testing.assert_allclose(base[name].loc[mask, "DailyReturn"], alternative[name].loc[mask, "DailyReturn"])
            self.assertFalse(np.allclose(base[name].DailyReturn.iloc[1:50], alternative[name].DailyReturn.iloc[1:50]))

    def test_vix_is_unrebased_signal_with_backcast_flag(self):
        inputs = self.inputs()
        out, diagnostics = make_histories(inputs, self.idx, self.rate, self.rate, self.a)
        pd.testing.assert_series_equal(out["VIX"].ResearchClose, inputs["VIX"], check_names=False)
        self.assertTrue(out["VIX"].SignalOnly.all())
        self.assertTrue(out["VIX"].HistoricalMethodologyBackcast.all())
        self.assertTrue(out["VIX"].DailyReturn.isna().all())
        self.assertTrue(all(not x["used_for_calibration"] for x in diagnostics["overlap_diagnostics"].values()))

    def test_input_set_cannot_silently_drop_a_fund(self):
        inputs = self.inputs()
        del inputs["CGDV"]
        with self.assertRaises(ResearchDataError):
            make_histories(inputs, self.idx, self.rate, self.rate, self.a)

    def test_fred_missing_release_not_zero(self):
        data = b"observation_date,DTB3\n1999-03-08,5.0\n1999-03-09,.\n1999-03-10,0.0\n"
        series = prep.parse_fred(data, "DTB3")
        self.assertEqual(len(series), 2)
        self.assertEqual(series.iloc[0], .05)
        self.assertEqual(series.iloc[1], 0)

    def test_fred_duplicate_dates_rejected(self):
        with self.assertRaises(ResearchDataError):
            prep.parse_fred(b"DATE,DFF\n1999-03-08,5\n1999-03-08,5\n", "DFF")

    def test_cboe_non_equity_dates_are_reported_not_silently_dropped(self):
        sessions = pd.to_datetime(["2020-01-03", "2020-01-06"])
        series, excluded = prep.parse_cboe(b"DATE,CLOSE\n01/03/2020,14\n01/04/2020,15\n01/06/2020,16\n", sessions)
        self.assertEqual(excluded, ["2020-01-04"])
        self.assertEqual(series.tolist(), [14, 16])

    def test_cboe_missing_equity_date_not_filled(self):
        with self.assertRaises(ResearchDataError):
            prep.parse_cboe(b"DATE,CLOSE\n01/03/2020,14\n", pd.to_datetime(["2020-01-03", "2020-01-06"]))


if __name__ == "__main__":
    unittest.main()
