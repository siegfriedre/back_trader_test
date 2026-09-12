"""Prepare explicitly hypothetical pre-listing histories on top of PR #1.

No strategy or execution engine. Run with --end YYYY-MM-DD (exclusive).
External downloads may fail; failed runs never publish usable research output.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil
import sys
import time
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np
import pandas as pd

from research_model import Assumptions, ResearchDataError, checked_close, make_histories, valid_index

ROOT = Path(__file__).resolve().parent
TARGETS = ["QQQ", "SPY", "QLD", "TQQQ", "SGOV", "VOO", "VTV", "SCHD", "CGDV", "KO"]
SUPPLEMENTS = {"BIL": "ETF", "VYM": "ETF", "VIVAX": "MUTUALFUND"}
SCENARIOS = {
    "base": Assumptions(),
    "higher_financing": Assumptions(annual_funding_spread=0.015),
    "broad_equity_proxy": Assumptions(style_proxy="broad_equity"),
    "combined_stress": Assumptions(annual_funding_spread=0.015, style_proxy="broad_equity"),
}
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
DEFAULT_OUTPUT = ROOT / "data" / "research"


def fetch_bytes(url: str) -> bytes:
    error = None
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "back-trader-research/1.0"})
            with urlopen(request, timeout=30) as response:
                data = response.read()
            if not data:
                raise ResearchDataError("Empty response")
            return data
        except Exception as exc:
            error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise ResearchDataError(f"Download failed: {url}: {error}")


def parse_fred(data: bytes, series_id: str) -> pd.Series:
    frame = pd.read_csv(io.BytesIO(data), na_values=["."])
    if len(frame.columns) != 2 or frame.columns[1] != series_id or frame.columns[0] not in {"DATE", "observation_date"}:
        raise ResearchDataError(f"Unexpected FRED schema for {series_id}")
    index = pd.DatetimeIndex(pd.to_datetime(frame.iloc[:, 0], errors="raise"))
    valid_index(index)
    values = pd.to_numeric(frame[series_id], errors="raise")
    # Preserve the full response on disk. Missing releases are NOT zero interest.
    result = pd.Series(values.to_numpy(dtype=float) / 100, index=index, name=series_id).dropna()
    if result.empty or not np.isfinite(result.to_numpy()).all():
        raise ResearchDataError(f"Invalid FRED values: {series_id}")
    return result


def parse_cboe(data: bytes, sessions: pd.DatetimeIndex) -> tuple[pd.Series, list[str]]:
    frame = pd.read_csv(io.BytesIO(data))
    if not {"DATE", "CLOSE"}.issubset(frame):
        raise ResearchDataError("Unexpected Cboe CSV schema")
    index = pd.DatetimeIndex(pd.to_datetime(frame.DATE, format="%m/%d/%Y", errors="raise"))
    valid_index(index)
    series = pd.Series(pd.to_numeric(frame.CLOSE, errors="raise").to_numpy(dtype=float), index=index, name="VIX")
    if not np.isfinite(series.to_numpy()).all() or (series <= 0).any():
        raise ResearchDataError("Invalid Cboe VIX levels")
    selected = series.loc[(series.index >= sessions[0]) & (series.index <= sessions[-1])]
    # Index publication days need not equal equity sessions. Record exclusions,
    # never silently discard or repair any missing requested equity session.
    excluded = selected.index.difference(sessions).strftime("%Y-%m-%d").tolist()
    aligned = selected.reindex(sessions)
    return checked_close(aligned, sessions, "CBOE_VIX"), excluded


def validate_supplement(raw: pd.DataFrame, metadata: dict, name: str, sessions: pd.DatetimeIndex) -> pd.Series:
    import get_data as gd
    frame = gd.normalize_daily(raw)
    expected_type = SUPPLEMENTS[name]
    if (metadata.get("symbol") != name or metadata.get("currency") != "USD"
            or metadata.get("instrumentType") != expected_type
            or metadata.get("exchangeTimezoneName") not in {"America/New_York", "US/Eastern"}):
        raise ResearchDataError(f"Wrong supplemental instrument metadata: {name}")
    first = metadata.get("firstTradeDate")
    if not isinstance(first, (int, float)) or isinstance(first, bool) or not np.isfinite(first):
        raise ResearchDataError(f"Invalid provider history start: {name}")
    first_date = pd.Timestamp(first, unit="s", tz="UTC").tz_convert("America/New_York").tz_localize(None).normalize()
    if not frame.index.equals(sessions[sessions >= first_date]):
        raise ResearchDataError(f"{name}: incomplete/unexpected provider sessions")
    required = gd.REQUIRED + (["Capital Gains"] if name == "VIVAX" else [])
    if not set(required).issubset(frame) or not np.isfinite(frame[required].to_numpy(dtype=float)).all():
        raise ResearchDataError(f"{name}: missing supplemental fields")
    if (frame[["Dividends", "Stock Splits"]] < 0).any().any() or (name == "VIVAX" and (frame["Capital Gains"] < 0).any()):
        raise ResearchDataError(f"{name}: invalid corporate actions")
    gd.validate_ohlc(frame, index_instrument=name == "VIVAX")
    return checked_close(frame["Adj Close"], sessions, name)


def collect_inputs(run: Path, start: str, end: str, verified_root: Path, reuse: bool) -> tuple[dict, dict, dict]:
    import get_data as gd
    sessions = gd.trading_sessions(start, end)
    if not reuse:
        gd.create_snapshot(start, end, TARGETS, verified_root)
    with gd.snapshot_lock(verified_root):
        frames = gd.load_verified_data(TARGETS, end=end, output_root=verified_root)
        pointer = json.loads((verified_root / "latest.json").read_text(encoding="utf-8"))
        observed_run = verified_root / "runs" / pointer["run_id"]
        manifest = json.loads((observed_run / "manifest.json").read_text(encoding="utf-8"))
        if manifest["start_inclusive"] != start:
            raise ResearchDataError("Verified snapshot start differs from research start")
        shutil.copytree(observed_run, run / "inputs" / "observed")
        for relative, expected_hash in manifest["files"].items():
            if gd.sha256(run / "inputs" / "observed" / relative) != expected_hash:
                raise ResearchDataError("Observed input changed while copying")
    closes = {n: frames[n].Close for n in TARGETS}
    source_meta = {"observed_run_id": pointer["run_id"], "observed_manifest_sha256": pointer["manifest_sha256"],
                   "sources": {}, "independently_verified_equity_prices": False}
    extra = run / "inputs" / "supplemental"
    extra.mkdir(parents=True)
    for name in SUPPLEMENTS:
        print(f"Downloading supplemental source {name}...", flush=True)
        raw, meta = gd.fetch_yahoo(name, start, end)
        raw.to_csv(extra / f"{name}.csv", index_label="Date")
        (extra / f"{name}.metadata.json").write_text(json.dumps(meta, default=str, indent=2), encoding="utf-8")
        closes[name] = validate_supplement(raw, meta, name, sessions)
        source_meta["sources"][name] = {"provider": "Yahoo via yfinance", "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                                         "role": "observed_proxy_NAV" if name == "VIVAX" else "observed_proxy_ETF"}
    rates = {}
    # Extra pre-start history is required for causal rate selection and holidays.
    rate_start = (pd.Timestamp(start) - pd.Timedelta(days=32)).date().isoformat()
    for series_id in ("DFF", "DTB3"):
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={rate_start}&coed={end}"
        data = fetch_bytes(url)
        (extra / f"{series_id}.csv").write_bytes(data)
        rates[series_id] = parse_fred(data, series_id)
        source_meta["sources"][series_id] = {"url": url, "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                                             "input_units": "percent_per_year", "is_point_in_time_vintage": False}
    data = fetch_bytes(CBOE_URL)
    (extra / "CBOE_VIX.csv").write_bytes(data)
    closes["VIX"], excluded = parse_cboe(data, sessions)
    source_meta["sources"]["VIX"] = {"url": CBOE_URL, "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                                      "signal_only": True, "excluded_non_equity_session_dates": excluded,
                                      "pre_2004_flag": "conservative_backcast_window_including_2003_transition_year"}
    return closes, rates, source_meta


def crisis_report(outputs: dict[str, pd.DataFrame], sessions: pd.DatetimeIndex) -> dict:
    import get_data as gd
    report = {}
    for label, left, right in (("dotcom_2000_2002", "2000-01-01", "2003-01-01"),
                               ("gfc_2007_2009", "2007-01-01", "2010-01-01")):
        # Full explicit windows, not peak-to-trough cherry picking.
        mask = (sessions >= left) & (sessions < right)
        expected = gd.trading_sessions(left, right)
        if not sessions[mask].equals(expected) or sessions[0] >= expected[0]:
            raise ResearchDataError(f"Requested data does not fully cover crisis window {label}")
        start_pos = int(np.flatnonzero(mask)[0])
        report[label] = {}
        for name, frame in outputs.items():
            if name == "VIX":
                report[label][name] = {"signal_only": True, "maximum_level": float(frame.loc[mask, "ResearchClose"].max())}
                continue
            curve = frame.ResearchClose.iloc[start_pos - 1: int(np.flatnonzero(mask)[-1]) + 1]
            report[label][name] = {"buy_and_hold_total_return": float(curve.iloc[-1] / curve.iloc[0] - 1),
                                   "buy_and_hold_max_drawdown": float((curve / curve.cummax() - 1).min()),
                                   "synthetic_return_rows": int(frame.loc[mask, "IsSyntheticReturn"].sum()),
                                   "rows": int(mask.sum()), "strategy_result": False}
    return report


def create_research_snapshot(start: str, end: str, verified_root: Path, output: Path, reuse: bool = False) -> Path:
    import get_data as gd
    output, verified_root = Path(output), Path(verified_root)
    if output.resolve() == verified_root.resolve() or output.resolve().is_relative_to(verified_root.resolve()) or verified_root.resolve().is_relative_to(output.resolve()):
        raise ResearchDataError("Research and observed snapshot roots must be separate, non-nested directories")
    gd.dates(start, end)
    with gd.snapshot_lock(output):
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
        run = output / "runs" / run_id
        run.mkdir(parents=True)
        pointer = {"schema": 1, "run_id": run_id, "status": "building"}
        gd.atomic_json(output / "latest.json", pointer)
        manifest = {"schema": 1, "run_id": run_id, "status": "building", "contains_synthetic": True,
                    "start_inclusive": start, "end_exclusive": end, "files": {},
                    "execution_contract": "close_return_only", "point_in_time_certified": False,
                    "normalized_prices_are_not_dollar_prices": True,
                    "scenarios": {k: v.to_dict() for k, v in SCENARIOS.items()},
                    "code_sha256": {p: gd.sha256(ROOT / p) for p in ("research_model.py", "prepare_research.py", "get_data.py")}}
        try:
            closes, rates, meta = collect_inputs(run, start, end, verified_root, reuse)
            manifest.update(meta)
            sessions = gd.trading_sessions(start, end)
            reports = {}
            for scenario, assumptions in SCENARIOS.items():
                outputs, diagnostics = make_histories(closes, sessions, rates["DFF"], rates["DTB3"], assumptions)
                folder = run / "scenarios" / scenario
                folder.mkdir(parents=True)
                for name, frame in outputs.items():
                    if not frame.IndicatorsReady.any():
                        raise ResearchDataError(f"{name}: no warmed-up indicators")
                    frame.to_csv(folder / f"{name}.csv", index_label="Date", date_format="%Y-%m-%d")
                for key in ("funding_inputs", "cash_inputs"):
                    diagnostics.pop(key).to_csv(folder / f"{key}.csv", index_label="Date", date_format="%Y-%m-%d")
                reports[scenario] = {"crisis_checks": crisis_report(outputs, sessions), **diagnostics}
            gd.atomic_json(run / "quality_report.json", reports)
            manifest["files"] = {p.relative_to(run).as_posix(): gd.sha256(p) for p in sorted(run.rglob("*")) if p.is_file()}
            manifest.update(status="passed", completed_at_utc=datetime.now(timezone.utc).isoformat())
            gd.atomic_json(run / "manifest.json", manifest)
            pointer.update(status="passed", manifest_sha256=gd.sha256(run / "manifest.json"))
            gd.atomic_json(output / "latest.json", pointer)
        except BaseException as exc:
            pointer.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            manifest.update(status="failed", error=pointer["error"])
            gd.atomic_json(output / "latest.json", pointer)
            gd.atomic_json(run / "manifest.json", manifest)
            raise
    return run


def load_research_data(*, end: str, scenario: str, allow_synthetic: bool = False,
                       allow_historical_backcast: bool = False, execution: str | None = None,
                       output: Path = DEFAULT_OUTPUT) -> dict[str, pd.DataFrame]:
    import get_data as gd
    if not allow_synthetic or execution != "close_return_only":
        raise ResearchDataError("Explicit allow_synthetic=True and execution='close_return_only' required")
    if scenario not in SCENARIOS:
        raise ResearchDataError("Unknown scenario")
    root = Path(output)
    try:
        if (root / ".download.lock").exists():
            raise ResearchDataError("Research run is being updated")
        initial = (root / "latest.json").read_bytes()
        pointer = json.loads(initial)
        run_id = pointer["run_id"]
        if pointer.get("schema") != 1 or pointer.get("status") != "passed" or not isinstance(run_id, str) or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ResearchDataError("Latest research run did not pass or has invalid path")
        run = root / "runs" / run_id
        if gd.sha256(run / "manifest.json") != pointer["manifest_sha256"]:
            raise ResearchDataError("Manifest checksum mismatch")
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("schema") != 1 or manifest.get("run_id") != run_id or manifest.get("status") != "passed"
                or manifest.get("contains_synthetic") is not True or manifest.get("end_exclusive") != end
                or manifest.get("execution_contract") != execution):
            raise ResearchDataError("Research snapshot contract/cutoff mismatch")
        inventory = manifest["files"]
        required = {f"scenarios/{s}/{n}.csv" for s in SCENARIOS for n in TARGETS + ["VIX", "funding_inputs", "cash_inputs"]} | {"quality_report.json"}
        if not required.issubset(inventory) or not any(p.startswith("inputs/observed/raw/") for p in inventory):
            raise ResearchDataError("Incomplete research inventory")
        for relative, digest in inventory.items():
            path = run / relative
            if not path.resolve().is_relative_to(run.resolve()) or gd.sha256(path) != digest:
                raise ResearchDataError(f"Unsafe path or checksum mismatch: {relative}")
        expected_sessions = gd.trading_sessions(manifest["start_inclusive"], end)
        frames = {}
        for name in TARGETS + ["VIX"]:
            frame = pd.read_csv(run / "scenarios" / scenario / f"{name}.csv", index_col="Date", parse_dates=["Date"])
            if not frame.index.equals(expected_sessions):
                raise ResearchDataError(f"Wrong session coverage: {name}")
            checked_close(frame.ResearchClose, expected_sessions, name)
            if name == "VIX" and frame.HistoricalMethodologyBackcast.any() and not allow_historical_backcast:
                raise ResearchDataError("Explicit historical VIX methodology-backcast acknowledgement required")
            frames[name] = frame
        if initial != (root / "latest.json").read_bytes() or (root / ".download.lock").exists():
            raise ResearchDataError("Research snapshot changed while reading")
        return frames
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if isinstance(exc, ResearchDataError):
            raise
        raise ResearchDataError(f"Research snapshot cannot be loaded: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="1999-03-10")
    parser.add_argument("--end", required=True, help="Exclusive date, fixed for reproducibility")
    parser.add_argument("--verified-root", type=Path, default=ROOT / "data" / "verified")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reuse-verified", action="store_true")
    args = parser.parse_args(argv)
    try:
        run = create_research_snapshot(args.start, args.end, args.verified_root, args.output, args.reuse_verified)
        print(f"Research snapshot: {run}\nHypothetical close-only history; not actual pre-listing fund performance.")
        return 0
    except Exception as exc:
        print(f"Research preparation FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
