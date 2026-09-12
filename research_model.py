"""Close-only counterfactual histories. Inputs must be audited observed series.

These functions never certify source accuracy, fit future data, invent OHLCV,
or use proxy returns to repair missing post-listing prices. ResearchClose is a
rebased return index, NOT a historical dollar/share price. See RESEARCH_DATA.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


class ResearchDataError(ValueError):
    pass


@dataclass(frozen=True)
class Assumptions:
    # Scenario assumptions, NOT a claim about actual historical fund fees.
    annual_fund_fee: float = 0.01
    annual_funding_spread: float = 0.005
    annual_cash_fee: float = 0.001
    rate_publication_lag_days: int = 2
    max_rate_age_days: int = 14
    bill_maturity_days: int = 91
    style_proxy: str = "dividend_value"

    def __post_init__(self):
        for name in ("annual_fund_fee", "annual_funding_spread", "annual_cash_fee"):
            value = getattr(self, name)
            if not np.isfinite(value) or not 0 <= value < 1:
                raise ResearchDataError(f"Invalid annual assumption: {name}")
        for name in ("rate_publication_lag_days", "max_rate_age_days", "bill_maturity_days"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ResearchDataError(f"Expected a positive integer: {name}")
        if self.style_proxy not in {"dividend_value", "broad_equity"}:
            raise ResearchDataError("Unknown style proxy")

    def to_dict(self) -> dict:
        return asdict(self)


def valid_index(index: pd.DatetimeIndex) -> None:
    if (not isinstance(index, pd.DatetimeIndex) or index.empty or index.tz is not None
            or index.hasnans or index.has_duplicates or not index.is_monotonic_increasing
            or not index.equals(index.normalize())):
        raise ResearchDataError("Expected unique sorted timezone-naive daily dates")


def checked_close(series: pd.Series, sessions: pd.DatetimeIndex, name: str) -> pd.Series:
    """Allow leading pre-observation absence only; disallow interior/trailing gaps."""
    valid_index(sessions)
    valid_index(series.index)
    try:
        out = pd.to_numeric(series, errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise ResearchDataError(f"{name}: invalid close values") from exc
    if not np.isfinite(out.to_numpy()).all() or (out <= 0).any():
        raise ResearchDataError(f"{name}: nonfinite/nonpositive observed close")
    # Caller must check the actual provider history start separately.
    if not out.index.equals(sessions[sessions >= out.index[0]]):
        raise ResearchDataError(f"{name}: unexpected or missing post-observation session")
    return out


def observed_returns(series: pd.Series, sessions: pd.DatetimeIndex, name: str) -> pd.Series:
    return checked_close(series, sessions, name).pct_change(fill_method=None).reindex(sessions)


def lagged_rate(rate: pd.Series, sessions: pd.DatetimeIndex, assumptions: Assumptions) -> pd.DataFrame:
    """Select a rate assumed published BEFORE the beginning of each return interval.

    FRED values are revised historical observations, not point-in-time vintages.
    A fixed publication lag is a conservative modeling convention, not a release calendar.
    """
    valid_index(sessions)
    valid_index(rate.index)
    values = pd.to_numeric(rate, errors="raise").astype(float)
    if not np.isfinite(values.to_numpy()).all() or ((values < -0.1) | (values > 1)).any():
        raise ResearchDataError("Rates must be finite annual decimal fractions, not percentages")
    rows = []
    for previous, current in zip(sessions[:-1], sessions[1:]):
        cutoff = previous - pd.Timedelta(days=assumptions.rate_publication_lag_days)
        pos = values.index.searchsorted(cutoff, side="right") - 1
        if pos < 0:
            raise ResearchDataError(f"No previously available rate for {current.date()}")
        observed = values.index[pos]
        if (previous - observed).days > assumptions.max_rate_age_days:
            raise ResearchDataError(f"Stale rate for {current.date()}")
        rows.append((current, values.iloc[pos], observed, (current - previous).days))
    result = pd.DataFrame(rows, columns=["Date", "AnnualRate", "RateObservationDate", "CalendarDays"])
    return result.set_index("Date").reindex(sessions)


def leveraged_returns(proxy_returns: pd.Series, rate_inputs: pd.DataFrame,
                       leverage: float, assumptions: Assumptions) -> pd.Series:
    if not np.isfinite(leverage) or leverage < 1:
        raise ResearchDataError("Only finite long leverage >= 1 is supported")
    if not proxy_returns.index.equals(rate_inputs.index):
        raise ResearchDataError("Rate/proxy dates differ")
    dt = rate_inputs.CalendarDays
    result = (leverage * proxy_returns
              - (leverage - 1) * (rate_inputs.AnnualRate + assumptions.annual_funding_spread) * dt / 360
              - assumptions.annual_fund_fee * dt / 365)
    return result


def cash_returns(discount_inputs: pd.DataFrame, assumptions: Assumptions) -> pd.Series:
    # For a fixed T-day discount bill: P/F = 1 - d*T/360.
    # Approximate carry over dt days = d*dt/(360-d*T), less assumed expenses.
    d = discount_inputs.AnnualRate
    denominator = 360 - d * assumptions.bill_maturity_days
    if (denominator.dropna() <= 0).any():
        raise ResearchDataError("Invalid bill discount yield")
    return (d * discount_inputs.CalendarDays / denominator
            - assumptions.annual_cash_fee * discount_inputs.CalendarDays / 365)


def proxy_chain(sessions: pd.DatetimeIndex, candidates: list[tuple[str, pd.Series]]) -> tuple[pd.Series, pd.Series]:
    """Later candidates replace earlier proxies only when two real closes exist."""
    result = pd.Series(np.nan, index=sessions, dtype=float)
    source = pd.Series("unavailable", index=sessions, dtype=object)
    for name, close in candidates:
        returns = observed_returns(close, sessions, name)
        mask = returns.notna()
        result.loc[mask], source.loc[mask] = returns.loc[mask], name
    if result.iloc[1:].isna().any():
        raise ResearchDataError("Proxy chain does not cover the full requested history")
    return result, source


def splice_returns(name: str, observed: pd.Series, sessions: pd.DatetimeIndex,
                   proxy: pd.Series | None = None, proxy_source: pd.Series | None = None) -> pd.DataFrame:
    """Forward rebasing avoids anchoring old prices to a future listing price.

    On the first observed day after a synthetic period, use a labeled proxy
    transition return; an actual close-to-close ETF return is not yet observable.
    From the SECOND observed day onward, use observed returns only.
    """
    close = checked_close(observed, sessions, name)
    actual = close.pct_change(fill_method=None).reindex(sessions)
    first = sessions.get_loc(close.index[0])
    if first:
        if proxy is None or proxy_source is None or not proxy.index.equals(sessions) or not proxy_source.index.equals(sessions):
            raise ResearchDataError(f"{name}: explicit full-length pre-listing model required")
        returns = proxy.copy()
        sources = proxy_source.astype(str).copy()
        synthetic = pd.Series(True, index=sessions)
        # Include the transition interval ending on the first observed close.
        returns.iloc[first + 1:] = actual.iloc[first + 1:]
        sources.iloc[first + 1:] = "observed:" + name
        sources.iloc[first] = "transition:" + sources.iloc[first]
        synthetic.iloc[first + 1:] = False
    else:
        returns = actual.copy()
        sources = pd.Series("observed:" + name, index=sessions)
        synthetic = pd.Series(False, index=sessions)
    returns.iloc[0] = np.nan  # First row is a level, never a fabricated zero daily return.
    if not np.isfinite(returns.iloc[1:].to_numpy()).all():
        raise ResearchDataError(f"{name}: missing/nonfinite modeled return")
    if (returns.iloc[1:] <= -1).any():
        day = returns.index[returns <= -1][0]
        raise ResearchDataError(f"{name}: model ruin on {day.date()}; no clipping or resurrection")
    wealth = np.exp(np.r_[0.0, np.log1p(returns.iloc[1:].to_numpy()).cumsum()]) * 100
    if not np.isfinite(wealth).all() or (wealth <= 0).any():
        raise ResearchDataError(f"{name}: numerical under/overflow in modeled index")
    out = pd.DataFrame({
        "ResearchClose": wealth, "DailyReturn": returns,
        "ObservedAdjustedClose": close.reindex(sessions),
        "ReturnSource": sources, "IsSyntheticReturn": synthetic,
        "HasObservedClose": sessions >= close.index[0], "SignalOnly": False,
        "PointInTimeCertified": False,
    }, index=sessions)
    out.index.name = "Date"
    return out


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ResearchDataError("RSI period must be a positive integer")
    out = pd.Series(np.nan, index=close.index, dtype=float)
    if len(close) <= period:
        return out
    delta = close.diff()
    up, down = delta.clip(lower=0), -delta.clip(upper=0)
    gain, loss = up.iloc[1:period + 1].mean(), down.iloc[1:period + 1].mean()
    for i in range(period, len(close)):
        if i > period:
            gain = ((period - 1) * gain + up.iloc[i]) / period
            loss = ((period - 1) * loss + down.iloc[i]) / period
        out.iloc[i] = 50 if gain == loss == 0 else 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    cols = []
    for p in (6, 14):
        col = f"RSI_{p}"
        out[col] = wilder_rsi(out.ResearchClose, p)
        cols.append(col)
    for p in (5, 10, 120, 200):
        col = f"MA_{p}"
        out[col] = out.ResearchClose.rolling(p).mean()
        cols.append(col)
    out["IndicatorsReady"] = out[cols].notna().all(axis=1)
    return out


def make_histories(observed: dict[str, pd.Series], sessions: pd.DatetimeIndex,
                   funding: pd.Series, discount_yield: pd.Series,
                   assumptions: Assumptions) -> tuple[dict[str, pd.DataFrame], dict]:
    required = {"QQQ", "SPY", "QLD", "TQQQ", "SGOV", "VOO", "VTV", "SCHD", "CGDV", "KO", "VIVAX", "VYM", "BIL", "VIX"}
    if set(observed) != required:
        raise ResearchDataError(f"Unexpected/missing input set: {set(observed) ^ required}")
    closes = {n: checked_close(v, sessions, n) for n, v in observed.items()}
    for n in ("QQQ", "SPY", "KO", "VIVAX", "VIX"):
        if closes[n].index[0] != sessions[0]:
            raise ResearchDataError(f"{n} must cover the research start")
    fund_inputs = lagged_rate(funding, sessions, assumptions)
    cash_inputs = lagged_rate(discount_yield, sessions, assumptions)
    qqq = observed_returns(closes["QQQ"], sessions, "QQQ")
    spy, spy_src = proxy_chain(sessions, [("SPY", closes["SPY"])])
    value, value_src = proxy_chain(sessions, [("VIVAX", closes["VIVAX"])])
    dividend, dividend_src = proxy_chain(sessions, [("VIVAX", closes["VIVAX"]), ("VYM", closes["VYM"])])
    active, active_src = proxy_chain(sessions, [("VIVAX", closes["VIVAX"]), ("VTV", closes["VTV"])])
    if assumptions.style_proxy == "broad_equity":
        dividend, dividend_src, active, active_src = spy, spy_src, spy, spy_src
    cash = cash_returns(cash_inputs, assumptions)
    cash_src = pd.Series("DTB3_carry_model", index=sessions)
    bil = observed_returns(closes["BIL"], sessions, "BIL")
    cash.loc[bil.notna()] = bil.loc[bil.notna()]
    cash_src.loc[bil.notna()] = "BIL"
    models = {
        "QLD": (leveraged_returns(qqq, fund_inputs, 2, assumptions), pd.Series("QQQ_2x_funding_model", index=sessions)),
        "TQQQ": (leveraged_returns(qqq, fund_inputs, 3, assumptions), pd.Series("QQQ_3x_funding_model", index=sessions)),
        "VOO": (spy, spy_src), "VTV": (value, value_src),
        "SCHD": (dividend, dividend_src), "CGDV": (active, active_src), "SGOV": (cash, cash_src),
    }
    outputs, diagnostics = {}, {}
    for name in ("QQQ", "SPY", "QLD", "TQQQ", "SGOV", "VOO", "VTV", "SCHD", "CGDV", "KO"):
        model, source = models.get(name, (None, None))
        out = splice_returns(name, closes[name], sessions, model, source)
        out["ProxyRisk"] = np.where(out.IsSyntheticReturn,
                                    "style_proxy_high" if name in {"CGDV", "SCHD", "VTV"} else "model_approximation", "observed_source_risk")
        outputs[name] = add_indicators(out)
        if model is not None:
            actual = observed_returns(closes[name], sessions, name)
            pair = pd.DataFrame({"actual": actual, "model": model}).dropna()
            valid = pair[np.isfinite(pair).all(axis=1) & (pair.model > -1)]
            corr = valid.actual.corr(valid.model) if len(valid) > 2 and valid.actual.std() > 0 and valid.model.std() > 0 else np.nan
            diagnostics[name] = {
                "overlap_rows": len(pair), "usable_overlap_rows": len(valid),
                "model_ruin_days_in_overlap": int((pair.model <= -1).sum()),
                "daily_correlation": float(corr) if pd.notna(corr) else None,
                "annualized_mean_return_error": float((valid.model - valid.actual).mean() * 252) if len(valid) else None,
                "annualized_tracking_error": float((valid.model - valid.actual).std() * np.sqrt(252)) if len(valid) > 1 else None,
                "used_for_calibration": False,
            }
    # VIX is an index LEVEL for thresholds, never rebased or tradable.
    vix = pd.DataFrame({"ResearchClose": closes["VIX"], "ObservedAdjustedClose": closes["VIX"],
                        "DailyReturn": np.nan, "ReturnSource": "CBOE_VIX_signal",
                        "IsSyntheticReturn": False, "HasObservedClose": True,
                        "SignalOnly": True, "PointInTimeCertified": False,
                        "ProxyRisk": "historical_index"}, index=sessions)
    # Use a conservative year boundary; do not pretend to know exact launch-day availability.
    vix["HistoricalMethodologyBackcast"] = sessions < pd.Timestamp("2004-01-01")
    outputs["VIX"] = add_indicators(vix)
    outputs["VIX"].index.name = "Date"
    return outputs, {"overlap_diagnostics": diagnostics,
                     "funding_inputs": fund_inputs, "cash_inputs": cash_inputs}
