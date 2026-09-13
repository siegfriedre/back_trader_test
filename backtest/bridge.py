"""Clarified bear-market ROC bridge; no date-specific trading exceptions.

A fresh ROC crossunder while already below MA starts an unleveraged QQQ
bridge. It persists until MA is recovered, even if ROC changes sign again.
An initial negative ROC alone is not a new cross. A fresh MA breakdown has
priority and requires a later ROC crossunder before another bridge entry.
"""
import numpy as np
import pandas as pd

BRIDGE = 'BEAR_QQQ'


def bridge_states(signal: pd.DataFrame, cfg) -> np.ndarray:
    state, candidate, count = 'WAIT', '', 0
    bull, tier, bridge = False, 'DEFENSIVE', False
    previous_roc = np.nan
    result = []
    for row in signal.itertuples():
        if not row.ready:
            result.append('WAIT')
            # A known ROC before MA warm-up may still define a later true crossing.
            previous_roc = row.ROC
            continue
        was_bull = bull
        if row.QQQ > row.MA * (1 + cfg.trend_band_bps / 10000):
            bull = True
        elif row.QQQ < row.MA * (1 - cfg.trend_band_bps / 10000):
            bull = False
        if row.ROC < -cfg.roc_band_pct:
            tier = 'AGGRESSIVE'
        elif row.ROC > cfg.roc_band_pct:
            tier = 'DEFENSIVE'
        crossunder = (np.isfinite(previous_roc)
                      and previous_roc >= -cfg.roc_band_pct
                      and row.ROC < -cfg.roc_band_pct)
        if bull:
            bridge = False
            desired = tier
        elif was_bull:
            # Trend exit wins even if a ROC crossunder occurs on the same bar.
            bridge = False
            desired = 'BEAR'
        else:
            if crossunder:
                bridge = True
            desired = BRIDGE if bridge else 'BEAR'
        previous_roc = row.ROC
        if desired == candidate:
            count += 1
        else:
            candidate, count = desired, 1
        if count >= cfg.confirm_days:
            state = desired
        result.append(state)
    return np.asarray(result)
