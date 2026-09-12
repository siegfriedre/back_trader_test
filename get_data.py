"""Observed daily bars with provenance checks and fail-closed snapshot publication.

No proxy histories or missing-price fills. Consistency checks are not an
independent guarantee of the data vendor's accuracy.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "data" / "verified"
SYMBOLS = {
    "QQQ": "QQQ", "SPY": "SPY", "QLD": "QLD", "TQQQ": "TQQQ",
    "SGOV": "SGOV", "VIX": "^VIX", "VOO": "VOO", "VTV": "VTV",
    "SCHD": "SCHD", "CGDV": "CGDV", "KO": "KO",
}
OHLC = ["Open", "High", "Low", "Close"]
REQUIRED = OHLC + ["Adj Close", "Volume", "Dividends", "Stock Splits"]
INDICATORS = ["RSI_6", "RSI_14", "MA_5", "MA_10", "MA_120", "MA_200"]
HISTORY_OPTIONS = dict(
    interval="1d", auto_adjust=False, back_adjust=False, actions=True,
    repair=False, keepna=True, prepost=False, rounding=False,
    raise_errors=True, timeout=30,
)
SCHEMA = 1


class DataValidationError(ValueError):
    """The requested data contract cannot be satisfied."""


def ny_today() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def dates(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    left, right = pd.Timestamp(date.fromisoformat(start)), pd.Timestamp(date.fromisoformat(end))
    if left >= right or right.date() > ny_today():
        raise DataValidationError("Require start < end <= today's New York date (end exclusive).")
    return left, right


def trading_sessions(start: str, end: str) -> pd.DatetimeIndex:
    import exchange_calendars as xcals

    left, right = dates(start, end)
    # US equity sessions, including holidays/exceptional closures, also for VIX signals.
    cal = xcals.get_calendar("XNYS", start=left, end=right)
    sessions = cal.sessions[(cal.sessions >= left) & (cal.sessions < right)]
    if sessions.empty:
        raise DataValidationError("The requested interval has no equity trading sessions.")
    return sessions


def normalize_daily(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise DataValidationError("Provider returned no bars.")
    df = frame.copy()
    if isinstance(df.columns, pd.MultiIndex) or df.columns.has_duplicates:
        raise DataValidationError("Expected unique single-ticker columns.")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise DataValidationError("Expected a daily DatetimeIndex.")
    if df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    if df.index.hasnans or not df.index.equals(df.index.normalize()):
        raise DataValidationError("Invalid or intraday timestamps in daily data.")
    if df.index.has_duplicates or not df.index.is_monotonic_increasing:
        raise DataValidationError("Duplicate or unsorted dates; refusing silent deduplication.")
    df.index.name = "Date"
    return df


def validate_ohlc(df: pd.DataFrame, *, index_instrument: bool = False) -> None:
    if not set(OHLC + ["Volume"]).issubset(df.columns):
        raise DataValidationError("Missing OHLCV columns.")
    if not np.isfinite(df[OHLC + ["Volume"]].to_numpy(dtype=float)).all():
        raise DataValidationError("Missing/non-finite OHLCV data.")
    if (df[OHLC] <= 0).any().any():
        raise DataValidationError("Non-positive prices.")
    if (df.Volume < 0).any() or (not index_instrument and (df.Volume == 0).any()):
        raise DataValidationError("Non-tradable zero-volume bar or negative volume.")
    tolerance = df[OHLC].abs().max(axis=1) * 1e-6
    bad = df.High + tolerance < df[["Open", "Close", "Low"]].max(axis=1)
    bad |= df.Low - tolerance > df[["Open", "Close", "High"]].min(axis=1)
    if bad.any():
        raise DataValidationError(f"Inconsistent OHLC on {df.index[bad][0].date()}.")


def first_trade_date(metadata: dict, symbol: str) -> pd.Timestamp:
    expected_type = "INDEX" if symbol == "^VIX" else ("EQUITY" if symbol == "KO" else "ETF")
    if metadata.get("symbol") != symbol or metadata.get("currency") != "USD":
        raise DataValidationError(f"Unexpected symbol/currency metadata for {symbol}.")
    if metadata.get("instrumentType") != expected_type:
        raise DataValidationError(f"Unexpected instrument type for {symbol}.")
    tz = metadata.get("exchangeTimezoneName")
    if tz not in {"America/New_York", "US/Eastern"}:
        raise DataValidationError(f"Unrecognized exchange timezone: {tz}.")
    first = metadata.get("firstTradeDate")
    if first is None or isinstance(first, bool):
        raise DataValidationError("Missing firstTradeDate; cannot check leading gaps.")
    timestamp = (pd.Timestamp(first, unit="s", tz="UTC") if isinstance(first, (int, float))
                 else pd.Timestamp(first))
    if pd.isna(timestamp) or timestamp.tz is None:
        raise DataValidationError("firstTradeDate must include a timezone.")
    return timestamp.tz_convert(tz).tz_localize(None).normalize()


def validate_raw(df: pd.DataFrame, metadata: dict, symbol: str,
                 sessions: pd.DatetimeIndex) -> pd.Timestamp:
    missing = set(REQUIRED) - set(df.columns)
    if missing:
        raise DataValidationError(f"Missing provider fields: {sorted(missing)}.")
    if not np.isfinite(df[REQUIRED].to_numpy(dtype=float)).all():
        raise DataValidationError("Null/non-finite provider fields; refusing to fill them.")
    if (df["Adj Close"] <= 0).any() or (df[["Dividends", "Stock Splits"]] < 0).any().any():
        raise DataValidationError("Invalid adjustment or corporate-action data.")
    if "Capital Gains" in df:
        gains = df["Capital Gains"].to_numpy(dtype=float)
        if not np.isfinite(gains).all() or (gains < 0).any():
            raise DataValidationError("Invalid capital-gains distribution data.")
    validate_ohlc(df, index_instrument=symbol == "^VIX")
    first = first_trade_date(metadata, symbol)
    expected = sessions[sessions >= first]
    absent, extra = expected.difference(df.index), df.index.difference(expected)
    if expected.empty or len(absent) or len(extra):
        raise DataValidationError(
            f"{symbol}: session mismatch; missing={absent.strftime('%Y-%m-%d').tolist()[:8]}, "
            f"unexpected={extra.strftime('%Y-%m-%d').tolist()[:8]}."
        )
    if len(df) < 200:
        raise DataValidationError(f"{symbol}: only {len(df)} real bars; MA200 needs at least 200.")
    return first


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI: initial simple mean, then recursive smoothing; flat series = 50."""
    result = pd.Series(np.nan, index=series.index, dtype=float)
    if len(series) <= period:
        return result
    change = series.diff()
    up, down = change.clip(lower=0), -change.clip(upper=0)
    gain, loss = up.iloc[1:period + 1].mean(), down.iloc[1:period + 1].mean()
    for i in range(period, len(series)):
        if i > period:
            gain = (gain * (period - 1) + up.iloc[i]) / period
            loss = (loss * (period - 1) + down.iloc[i]) / period
        result.iloc[i] = (50.0 if gain == loss == 0 else
                          100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss))
    return result


def adjusted_daily(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = raw[OHLC + ["Volume"]].copy()
    factor = raw["Adj Close"] / raw["Close"]
    df[OHLC] = df[OHLC].mul(factor, axis=0)
    df["AdjustmentFactor"] = factor
    validate_ohlc(df, index_instrument=symbol == "^VIX")
    for period in (6, 14):
        df[f"RSI_{period}"] = calculate_rsi(df.Close, period)
    for period in (5, 10, 120, 200):
        df[f"MA_{period}"] = df.Close.rolling(period).mean()
    # Preserve all real warm-up rows, rather than discarding dates or fabricating history.
    df["IndicatorsReady"] = df[INDICATORS].notna().all(axis=1)
    df["IsSynthetic"] = False
    return df


def fetch_yahoo(symbol: str, start: str, end: str) -> tuple[pd.DataFrame, dict]:
    import yfinance as yf

    yf.set_tz_cache_location(str(ROOT / ".yf_cache"))
    last_error = None
    for attempt in range(3):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start, end=end, **HISTORY_OPTIONS)
            if df is None or df.empty:
                raise DataValidationError(f"{symbol}: empty download.")
            return df, ticker.get_history_metadata()
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise DataValidationError(f"{symbol}: download failed after 3 attempts: {last_error}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, content: dict) -> None:
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(content, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def snapshot_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".download.lock"
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise DataValidationError("Another run owns .download.lock; check it before retrying.") from exc
    try:
        with stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def create_snapshot(start: str, end: str, names: list[str],
                    output_root: Path = DEFAULT_OUTPUT) -> Path:
    dates(start, end)
    if not names or len(names) != len(set(names)) or set(names) - SYMBOLS.keys():
        raise DataValidationError("Select unique supported symbols.")
    root = Path(output_root)
    with snapshot_lock(root):
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
        run = root / "runs" / run_id
        run.mkdir(parents=True)
        pointer = {"schema": SCHEMA, "run_id": run_id, "status": "downloading"}
        # Invalidate the reader BEFORE downloading. An old batch is never a fallback.
        atomic_json(root / "latest.json", pointer)
        manifest = {
            "schema": SCHEMA, "run_id": run_id, "status": "downloading",
            "source": "Yahoo Finance via yfinance", "independently_verified": False,
            "start_inclusive": start, "end_exclusive": end, "requested_symbols": names,
            "history_options": HISTORY_OPTIONS, "calendar": "XNYS",
            "adjustment": "All OHLC multiplied by provider Adj Close / Close; volume unchanged",
            "contains_synthetic": False, "files": {}, "instruments": {},
        }
        try:
            manifest["versions"] = {name: version(name) for name in
                                    ("yfinance", "pandas", "numpy", "exchange-calendars")}
            manifest["python"] = sys.version.split()[0]
            manifest["script_sha256"] = sha256(Path(__file__))
            sessions = trading_sessions(start, end)
            for name in names:
                symbol = SYMBOLS[name]
                print(f"Downloading and checking {name} ({symbol})...", flush=True)
                response, metadata = fetch_yahoo(symbol, start, end)
                fetched_at = datetime.now(timezone.utc).isoformat()
                raw_path = run / "raw" / f"{name}.csv"
                raw_path.parent.mkdir(exist_ok=True)
                # Save the provider-returned table before normalization/validation.
                response.to_csv(raw_path, index_label="Date")
                metadata_path = run / "raw" / f"{name}.metadata.json"
                metadata_path.write_text(json.dumps(metadata, default=str, ensure_ascii=False,
                                                   indent=2), encoding="utf-8")
                raw = normalize_daily(response)
                first = validate_raw(raw, metadata, symbol, sessions)
                adjusted = adjusted_daily(raw, symbol)
                adjusted_path = run / "adjusted" / f"{name}.csv"
                adjusted_path.parent.mkdir(exist_ok=True)
                adjusted.to_csv(adjusted_path, index_label="Date", date_format="%Y-%m-%d")
                for path in (raw_path, metadata_path, adjusted_path):
                    manifest["files"][path.relative_to(run).as_posix()] = sha256(path)
                ready = adjusted.index[adjusted.IndicatorsReady]
                manifest["instruments"][name] = {
                    "symbol": symbol, "fetched_at_utc": fetched_at, "rows": len(raw),
                    "provider_first_trade_date": first.date().isoformat(),
                    "first_date": raw.index[0].date().isoformat(),
                    "last_date": raw.index[-1].date().isoformat(),
                    "indicators_ready_from": ready[0].date().isoformat(),
                    "tradable": name != "VIX",
                }
            manifest["common_indicators_start"] = max(
                item["indicators_ready_from"] for item in manifest["instruments"].values())
            manifest["status"] = "passed"
            manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
            atomic_json(run / "manifest.json", manifest)
            pointer.update(status="passed", manifest_sha256=sha256(run / "manifest.json"))
            atomic_json(root / "latest.json", pointer)
        except BaseException as exc:
            # Also invalidate on Ctrl+C; a killed process leaves status=downloading (blocked).
            pointer.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            atomic_json(root / "latest.json", pointer)
            manifest.update(status="failed", error=pointer["error"])
            atomic_json(run / "manifest.json", manifest)
            raise
    return run


def load_verified_data(names: list[str], *, end: str,
                       output_root: Path = DEFAULT_OUTPUT) -> dict[str, pd.DataFrame]:
    """Backtest gate: explicit cutoff, whole-batch integrity, no legacy fallback.

    Returns adjusted OHLC and warm-up rows. Check IndicatorsReady for signals;
    VIX is a signal input, not a tradable security.
    """
    root = Path(output_root)
    if not names or len(names) != len(set(names)) or set(names) - SYMBOLS.keys():
        raise DataValidationError("Select unique supported symbols.")
    try:
        pointer_bytes = (root / "latest.json").read_bytes()
        pointer = json.loads(pointer_bytes)
        if pointer.get("schema") != SCHEMA or pointer.get("status") != "passed":
            raise DataValidationError("Latest data preparation did not pass; backtest blocked.")
        run_id = pointer["run_id"]
        if not isinstance(run_id, str) or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise DataValidationError("Invalid snapshot path.")
        run = root / "runs" / run_id
        if sha256(run / "manifest.json") != pointer["manifest_sha256"]:
            raise DataValidationError("Snapshot manifest checksum mismatch.")
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("schema") != SCHEMA or manifest.get("status") != "passed"
                or manifest.get("contains_synthetic") is not False
                or manifest.get("run_id") != run_id):
            raise DataValidationError("Unapproved snapshot contract.")
        if manifest["end_exclusive"] != end:
            raise DataValidationError("Requested end date differs from snapshot; refusing stale data.")
        dates(manifest["start_inclusive"], end)
        available = manifest["requested_symbols"]
        if not available or set(available) - SYMBOLS.keys() or set(names) - set(available):
            raise DataValidationError("Snapshot is missing requested instruments.")
        expected_files = {f"{folder}/{name}{suffix}" for name in available for folder, suffix in
                          (("raw", ".csv"), ("raw", ".metadata.json"), ("adjusted", ".csv"))}
        if set(manifest["files"]) != expected_files:
            raise DataValidationError("Snapshot file inventory is incomplete.")
        for relative, expected_hash in manifest["files"].items():
            if sha256(run / relative) != expected_hash:
                raise DataValidationError(f"Data checksum mismatch: {relative}.")
        result = {}
        for name in names:
            df = pd.read_csv(run / "adjusted" / f"{name}.csv", index_col="Date", parse_dates=["Date"])
            df = normalize_daily(df)
            validate_ohlc(df, index_instrument=name == "VIX")
            if not df.IsSynthetic.eq(False).all() or not df.IndicatorsReady.any():
                raise DataValidationError(f"Invalid synthetic/warm-up markers: {name}.")
            result[name] = df
        if pointer_bytes != (root / "latest.json").read_bytes():
            raise DataValidationError("Snapshot changed while loading; retry against a stable run.")
        return result
    except (OSError, KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, DataValidationError):
            raise
        raise DataValidationError(f"Cannot load a complete verified snapshot: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="1999-03-10", help="Inclusive ISO date")
    parser.add_argument("--end", default=ny_today().isoformat(), help="Exclusive date; defaults to NY today")
    parser.add_argument("--symbols", nargs="+", choices=tuple(SYMBOLS), default=list(SYMBOLS))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify-only", action="store_true", help="Verify without downloading")
    args = parser.parse_args(argv)
    try:
        if args.verify_only:
            frames = load_verified_data(args.symbols, end=args.end, output_root=args.output)
            print(f"Snapshot checks passed for {len(frames)} instruments (end exclusive: {args.end}).")
        else:
            run = create_snapshot(args.start, args.end, args.symbols, args.output)
            print(f"All requested instruments passed. Snapshot: {run}")
        print("Source: Yahoo Finance; consistency checked, not independently verified against an exchange.")
        return 0
    except Exception as exc:
        print(f"Data preparation/verification FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
