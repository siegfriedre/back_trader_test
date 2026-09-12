"""Offline regressions. Fixtures are invented and must never be used for backtests."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import get_data as gd


class DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.start, cls.end = "2020-01-01", "2022-01-01"
        cls.sessions = gd.trading_sessions(cls.start, cls.end)

    def fixture(self, symbol="QQQ", start_index=0):
        index = self.sessions[start_index:]
        close = 100 + np.arange(len(index)) * 0.01
        df = pd.DataFrame({
            "Open": close - 0.1, "High": close + 1, "Low": close - 1,
            "Close": close, "Adj Close": close * 0.5, "Volume": 1000,
            "Dividends": 0.0, "Stock Splits": 0.0,
        }, index=index)
        metadata = {
            "symbol": symbol, "currency": "USD", "exchangeTimezoneName": "America/New_York",
            "instrumentType": "INDEX" if symbol == "^VIX" else "EQUITY" if symbol == "KO" else "ETF",
            "firstTradeDate": int((index[0].tz_localize("America/New_York") + pd.Timedelta(hours=9.5)).timestamp()),
        }
        return gd.normalize_daily(df), metadata

    def snapshot(self, root, names=None, fetch=None):
        with patch.object(gd, "fetch_yahoo", side_effect=fetch or (lambda s, *_: self.fixture(s))):
            with contextlib.redirect_stdout(io.StringIO()):
                return gd.create_snapshot(self.start, self.end, names or ["QQQ"], root)

    def test_all_ohlc_share_adjustment_and_volume_is_preserved(self):
        raw, _ = self.fixture()
        out = gd.adjusted_daily(raw, "QQQ")
        np.testing.assert_allclose(out[gd.OHLC], raw[gd.OHLC] * 0.5)
        pd.testing.assert_series_equal(out.Volume, raw.Volume)
        self.assertTrue(out.Close.equals(raw["Adj Close"].rename("Close")))
        self.assertFalse(out.IsSynthetic.any())

    def test_post_listing_only_and_warmup_is_retained(self):
        raw, meta = self.fixture("SGOV", 252)
        gd.validate_raw(raw, meta, "SGOV", self.sessions)
        out = gd.adjusted_daily(raw, "SGOV")
        self.assertTrue(out.index.equals(raw.index))
        self.assertFalse(out.IndicatorsReady.iloc[:199].any())
        self.assertTrue(out.IndicatorsReady.iloc[199:].all())
        self.assertNotEqual(out.Open.iloc[0], out.Close.iloc[0])

    def test_no_price_or_adjustment_fallback(self):
        for col in gd.REQUIRED:
            with self.subTest(field=col):
                raw, meta = self.fixture()
                raw.loc[raw.index[210], col] = np.nan
                with self.assertRaises(gd.DataValidationError):
                    gd.validate_raw(raw, meta, "QQQ", self.sessions)
        raw, meta = self.fixture()
        with self.assertRaisesRegex(gd.DataValidationError, "Missing provider fields"):
            gd.validate_raw(raw.drop(columns="Adj Close"), meta, "QQQ", self.sessions)

    def test_leading_interior_and_trailing_missing_sessions_rejected(self):
        for position in [0, 210, -1]:
            with self.subTest(position=position):
                raw, meta = self.fixture()
                with self.assertRaisesRegex(gd.DataValidationError, "session mismatch"):
                    gd.validate_raw(raw.drop(raw.index[position]), meta, "QQQ", self.sessions)

    def test_holidays_and_exceptional_closures_are_not_missing_bars(self):
        sessions = gd.trading_sessions("2001-09-07", "2001-09-19")
        self.assertEqual(list(sessions.strftime("%Y-%m-%d")), ["2001-09-07", "2001-09-10", "2001-09-17", "2001-09-18"])
        self.assertNotIn(pd.Timestamp("2020-12-25"), self.sessions)

    def test_unexpected_weekend_bars_rejected(self):
        raw, meta = self.fixture()
        raw.loc[pd.Timestamp("2020-01-04")] = raw.iloc[0]
        with self.assertRaisesRegex(gd.DataValidationError, "session mismatch"):
            gd.validate_raw(raw.sort_index(), meta, "QQQ", self.sessions)

    def test_duplicates_unsorted_intraday_dates_rejected(self):
        raw, _ = self.fixture()
        intraday = raw.copy()
        intraday.index += pd.Timedelta(hours=12)
        for bad in [pd.concat([raw, raw.iloc[-1:]]), raw.iloc[::-1], intraday]:
            with self.subTest(index=bad.index[:2]):
                with self.assertRaises(gd.DataValidationError):
                    gd.normalize_daily(bad)

    def test_new_york_daily_dates_survive_dst(self):
        raw, _ = self.fixture()
        aware = raw.copy()
        aware.index = aware.index.tz_localize("America/New_York")
        # tz conversion drops pandas' optional frequency hint, but must preserve dates/values.
        pd.testing.assert_frame_equal(raw, gd.normalize_daily(aware), check_freq=False)

    def test_invalid_ohlc_and_zero_equity_volume_rejected(self):
        for column, value in [("Close", 10000), ("High", 1), ("Low", 10000), ("Open", -1), ("Volume", 0)]:
            raw, meta = self.fixture()
            raw.loc[raw.index[0], column] = value
            with self.subTest(column=column), self.assertRaises(gd.DataValidationError):
                gd.validate_raw(raw, meta, "QQQ", self.sessions)
        raw, meta = self.fixture("^VIX")
        raw.Volume = 0
        gd.validate_raw(raw, meta, "^VIX", self.sessions)

    def test_missing_or_wrong_metadata_rejected(self):
        for key, value in [("firstTradeDate", None), ("symbol", "SPY"), ("currency", "EUR"), ("instrumentType", "INDEX")]:
            raw, meta = self.fixture()
            meta[key] = value
            with self.subTest(key=key), self.assertRaises(gd.DataValidationError):
                gd.validate_raw(raw, meta, "QQQ", self.sessions)

    def test_short_history_fails_before_empty_export(self):
        raw, meta = self.fixture(start_index=len(self.sessions) - 199)
        with self.assertRaisesRegex(gd.DataValidationError, "at least 200"):
            gd.validate_raw(raw, meta, "QQQ", self.sessions)

    def test_wilder_rsi_seed_and_flat_market(self):
        out = gd.calculate_rsi(pd.Series([10.0, 11.0, 10.0, 12.0, 11.0]), 3)
        self.assertTrue(out.iloc[:3].isna().all())
        self.assertAlmostEqual(out.iloc[3], 75.0)
        self.assertAlmostEqual(out.iloc[4], 54.54545454545455)
        for series, expected in [(pd.Series([10.0] * 20), 50.0), (pd.Series(range(20)), 100.0), (pd.Series(range(20, 0, -1)), 0.0)]:
            self.assertTrue(gd.calculate_rsi(series).iloc[14:].eq(expected).all())

    def test_future_prices_do_not_change_prior_indicators(self):
        raw, _ = self.fixture()
        full = gd.adjusted_daily(raw, "QQQ")
        prefix = gd.adjusted_daily(raw.iloc[:250], "QQQ")
        pd.testing.assert_frame_equal(full.iloc[:250], prefix)

    def test_snapshot_has_provenance_and_complete_readable_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = self.snapshot(root, ["QQQ", "SGOV"], lambda s, *_: self.fixture(s, 252 if s == "SGOV" else 0))
            manifest = json.loads((run / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "passed")
            self.assertFalse(manifest["independently_verified"])
            self.assertEqual(len(manifest["files"]), 6)
            self.assertEqual(manifest["common_indicators_start"], str(self.sessions[451].date()))
            frames = gd.load_verified_data(["QQQ", "SGOV"], end=self.end, output_root=root)
            self.assertEqual(len(frames["QQQ"]), len(self.sessions))
            self.assertEqual(frames["SGOV"].index[0], self.sessions[252])

    def test_failed_update_blocks_old_batch_without_destroying_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = self.snapshot(root)
            old_bytes = (old / "adjusted/QQQ.csv").read_bytes()
            def fetch(symbol, *_):
                if symbol == "TQQQ":
                    raise RuntimeError("provider unavailable")
                return self.fixture(symbol)
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                self.snapshot(root, ["QQQ", "TQQQ"], fetch)
            self.assertEqual((old / "adjusted/QQQ.csv").read_bytes(), old_bytes)
            self.assertEqual(json.loads((root / "latest.json").read_text())["status"], "failed")
            self.assertFalse((root / ".download.lock").exists())
            with self.assertRaisesRegex(gd.DataValidationError, "did not pass"):
                gd.load_verified_data(["QQQ"], end=self.end, output_root=root)

    def test_bad_data_is_preserved_for_audit_but_never_published(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw, meta = self.fixture()
            raw.iloc[10, raw.columns.get_loc("Open")] = np.nan
            with self.assertRaises(gd.DataValidationError):
                self.snapshot(root, fetch=lambda *_: (raw, meta))
            run_id = json.loads((root / "latest.json").read_text())["run_id"]
            self.assertTrue((root / "runs" / run_id / "raw/QQQ.csv").exists())
            with self.assertRaises(gd.DataValidationError):
                gd.load_verified_data(["QQQ"], end=self.end, output_root=root)

    def test_file_or_manifest_modification_blocks_loading(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = self.snapshot(root, ["QQQ", "TQQQ"])
            for relative in ["raw/TQQQ.csv", "raw/QQQ.metadata.json", "adjusted/QQQ.csv", "manifest.json"]:
                file = run / relative
                old = file.read_bytes()
                file.write_bytes(old + b"\n")
                with self.subTest(file=relative), self.assertRaisesRegex(gd.DataValidationError, "checksum"):
                    gd.load_verified_data(["QQQ"], end=self.end, output_root=root)
                file.write_bytes(old)

    def test_stale_cutoff_or_missing_symbol_blocks_loading(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.snapshot(root)
            for names, end in [(["QQQ"], "2022-01-02"), (["TQQQ"], self.end)]:
                with self.subTest(names=names), self.assertRaises(gd.DataValidationError):
                    gd.load_verified_data(names, end=end, output_root=root)

    def test_no_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "QQQ_synthetic_daily.csv").write_text("legacy")
            with self.assertRaises(gd.DataValidationError):
                gd.load_verified_data(["QQQ"], end=self.end, output_root=root)

    def test_active_run_cannot_be_overwritten_or_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with gd.snapshot_lock(root):
                with self.assertRaisesRegex(gd.DataValidationError, "Another run"):
                    self.snapshot(root)
            gd.atomic_json(root / "latest.json", {"schema": 1, "status": "downloading"})
            with self.assertRaisesRegex(gd.DataValidationError, "did not pass"):
                gd.load_verified_data(["QQQ"], end=self.end, output_root=root)

    def test_future_cutoff_is_rejected(self):
        with patch.object(gd, "ny_today", return_value=pd.Timestamp("2022-01-01").date()):
            with self.assertRaises(gd.DataValidationError):
                gd.dates(self.start, "2022-01-02")
            gd.dates(self.start, "2022-01-01")

    def test_download_options_and_bounded_retries(self):
        raw, meta = self.fixture()
        with patch("yfinance.set_tz_cache_location"), patch("yfinance.Ticker") as factory:
            ticker = factory.return_value
            ticker.history.side_effect = [RuntimeError("network error"), raw]
            ticker.get_history_metadata.return_value = meta
            with patch.object(gd.time, "sleep"):
                frame, metadata = gd.fetch_yahoo("QQQ", self.start, self.end)
            self.assertEqual(ticker.history.call_count, 2)
            options = ticker.history.call_args.kwargs
            self.assertFalse(options["auto_adjust"])
            self.assertFalse(options["back_adjust"])
            self.assertFalse(options["repair"])
            self.assertTrue(options["keepna"])
            self.assertTrue(options["actions"])
            self.assertEqual(options["end"], self.end)
            self.assertIs(frame, raw)
            self.assertIs(metadata, meta)
            ticker.history.reset_mock()
            ticker.history.side_effect = RuntimeError("still unavailable")
            with patch.object(gd.time, "sleep"), self.assertRaises(gd.DataValidationError):
                gd.fetch_yahoo("QQQ", self.start, self.end)
            self.assertEqual(ticker.history.call_count, 3)

    def test_interrupted_download_blocks_reader(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.snapshot(root)
            def interrupted(*_):
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                self.snapshot(root, fetch=interrupted)
            with self.assertRaises(gd.DataValidationError):
                gd.load_verified_data(["QQQ"], end=self.end, output_root=root)

    def test_cli_returns_failure_without_network_on_verify_failure(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gd.main(["--verify-only", "--output", temp, "--end", self.end]), 1)


if __name__ == "__main__":
    unittest.main()
