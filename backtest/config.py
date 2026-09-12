"""Frozen, reviewable experiment definitions. No parameter search or optimization."""
from dataclasses import asdict, dataclass
import math

STRATEGIES = {
    'hold_qqq': '固定 70% QQQ / 30% SGOV 基准',
    'hold_leveraged': '固定进攻配置基准',
    'ma200': 'A：仅 MA200 趋势开关',
    'ma_roc': 'B：MA200 + VTV/QQQ ROC35',
    'bull_rsi': 'C：B + 有上限的牛市 RSI 抄底',
    'bull_bear_rsi': 'D：C + 有上限的熊市 RSI 抄底',
    'user_rules': '原始描述解释版（不是参数优化版）',
}
# (instrument, aggressive weight). All defensive bull states are 70% QQQ.
# 70% QLD and 46 2/3% TQQQ both target ~1.4x initial daily exposure.
PROFILES = {'tqqq70': ('TQQQ', .70), 'qld70': ('QLD', .70),
            'qld100': ('QLD', 1.0), 'tqqq46': ('TQQQ', 1.4 / 3)}
WINDOWS = ('post_tqqq', 'long_history', 'all_observed')


@dataclass(frozen=True)
class Config:
    initial: float = 80000.0
    monthly: float = 1500.0
    cost_bps: float = 5.0  # all-in one-way commission + spread + slippage assumption
    ma_period: int = 200
    rsi_period: int = 6
    roc_period: int = 35
    entry_rsi: float = 21.0
    reset_rsi: float = 30.0
    exit_rsi: float = 50.0
    dip_fraction: float = .05
    dip_cap: float = .10
    max_hold: int = 10
    signal_lag: int = 1
    trend_band_bps: float = 0.0
    roc_band_pct: float = 0.0
    confirm_days: int = 1
    rebalance: str = 'monthly'
    funding_spread: float = .005
    fund_fee: float = .01
    cash_fee: float = .001
    rate_lag_days: int = 2
    max_rate_age: int = 14
    value_proxy: str = 'VIVAX'

    def __post_init__(self):
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and (isinstance(value, bool) or not math.isfinite(value)):
                raise ValueError(f'Invalid finite number: {key}')
        if self.initial <= 0 or self.monthly < 0 or not 0 <= self.cost_bps < 1000:
            raise ValueError('Require positive capital, nonnegative contributions, and 0 <= cost_bps < 1000')
        for key in ('ma_period', 'rsi_period', 'roc_period', 'max_hold', 'signal_lag',
                    'confirm_days', 'rate_lag_days', 'max_rate_age'):
            value = getattr(self, key)
            if not isinstance(value, int) or value < 1:
                raise ValueError(f'Expected positive integer: {key}')
        if not 0 < self.entry_rsi < self.reset_rsi <= self.exit_rsi <= 100:
            raise ValueError('Require 0 < entry RSI < reset RSI <= exit RSI <= 100')
        if not 0 < self.dip_fraction <= self.dip_cap <= .30:
            raise ValueError('Require 0 < dip_fraction <= dip_cap <= .30')
        if not 0 <= self.trend_band_bps < 2000 or not 0 <= self.roc_band_pct < 100:
            raise ValueError('Invalid signal buffer')
        if any(not 0 <= x < 1 for x in (self.funding_spread, self.fund_fee, self.cash_fee)):
            raise ValueError('Annual costs must be decimal fractions in [0,1)')
        if self.rebalance not in ('monthly', 'state_only') or self.value_proxy not in ('VIVAX', 'SPY'):
            raise ValueError('Unknown rebalance or value proxy')

    def to_dict(self):
        return asdict(self)
