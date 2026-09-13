"""Cash-flow-aware performance metrics, audit files, and an offline HTML report."""
from pathlib import Path
import html
import json
import math

import numpy as np
import pandas as pd


def clean(value):
    """Strict JSON: use null rather than NaN/Infinity; normalize NumPy scalars."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def write_json(path, data):
    Path(path).write_text(json.dumps(clean(data), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def xirr(flows, final_date, final_value):
    """Only initial/subsequent contributions and terminal mark; unique normal root.

    No dependency on scipy. Solve in log(1+r) space, avoiding r <= -100%.
    """
    if final_date <= flows[0][0]:
        return None
    all_flows = list(flows) + [(final_date, final_value)]
    t = np.array([(d - flows[0][0]).days / 365.2425 for d, _ in all_flows])
    v = np.array([x for _, x in all_flows], dtype=float)
    v /= np.abs(v).max()
    def npv(z):
        # rescaling a common exponential factor preserves signs and avoids overflow
        exponent = -z * t
        return float(np.dot(v, np.exp(exponent - exponent.max())))
    lo, hi = -30., 30.
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(120):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return math.expm1((lo + hi) / 2)


def dd_details(nav):
    """Initial unit value 1 is a high-water mark even if entry fees reduce NAV."""
    values, dates = nav.to_numpy(float), nav.index
    high, peak_date, deepest, trough_date, peak_for_trough = 1., dates[0], 0., dates[0], dates[0]
    recovery, longest = None, 0
    for day, value in zip(dates, values):
        if value >= high:
            high, peak_date = value, day
        else:
            longest = max(longest, (day - peak_date).days)
        draw = value / high - 1
        if draw < deepest:
            deepest, trough_date, peak_for_trough = draw, day, peak_date
    peak_value = max(1., float(nav.loc[:peak_for_trough].max()))
    if deepest < 0:
        later = nav.loc[nav.index > trough_date]
        recovered = later[later >= peak_value]
        recovery = str(recovered.index[0].date()) if len(recovered) else None
    else:
        recovery = str(dates[0].date())
    return {'max_drawdown': deepest, 'drawdown_peak_date': str(peak_for_trough.date()),
            'drawdown_trough_date': str(trough_date.date()), 'drawdown_recovered_date': recovery,
            'longest_underwater_calendar_days': longest}


def summarize(result):
    d = result.daily
    years = (d.index[-1] - d.index[0]).days / 365.2425
    nav = d.UnitNAV
    traded = sum(t['notional'] for t in result.trades)
    cost = sum(t['cost'] for t in result.trades)
    out = {'case_id': result.case_id, 'window': result.window, 'strategy': result.strategy,
           'profile': result.profile, 'start': str(d.index[0].date()), 'end': str(d.index[-1].date()),
           'sessions': len(d), 'final_equity': float(d.Equity.iloc[-1]),
           'contributed': float(d.Contributed.iloc[-1]),
           'net_profit': float(d.Equity.iloc[-1] - d.Contributed.iloc[-1]),
           'twr_total_return': float(nav.iloc[-1] - 1),
           'twr_cagr': float(nav.iloc[-1] ** (1 / years) - 1) if years > 0 else None,
           'money_weighted_xirr': xirr(result.cashflows, d.index[-1], float(d.Equity.iloc[-1])),
           'annualized_daily_volatility': float(d.DailyTWR.std(ddof=1) * np.sqrt(252)) if len(d) > 1 else None,
           'trade_legs': len(result.trades), 'traded_notional': traded, 'total_cost': cost,
           'regime_changes': sum(x['event'] == 'STATE_CHANGE' for x in result.events),
           'synthetic_held_return_days': int(d.SyntheticReturnExposure.gt(1e-12).sum()),
           'proxy_signal_days': int(d.ProxyInROCSignal.sum()),
           'average_approx_exposure': float(d.ApproxDailyExposure.mean()),
           'maximum_approx_exposure': float(d.ApproxDailyExposure.max()),
           'first_allocation': next((x['execution_date'] for x in result.events if x['event'] == 'INITIAL_ALLOCATION'), None),
           **dd_details(nav)}
    return clean(out)


def annual_returns(result):
    d, previous = result.daily, 1.
    rows = []
    for year, part in d.groupby(d.index.year):
        curve = part.UnitNAV / previous
        dd = curve.to_numpy() / np.maximum.accumulate(np.r_[1., curve])[1:] - 1
        rows.append({'case_id': result.case_id, 'year': int(year), 'start': str(part.index[0].date()),
                     'end': str(part.index[-1].date()), 'twr_return': float(curve.iloc[-1] - 1),
                     'window_drawdown': float(dd.min()), 'contributions': float(part.Contribution.sum())})
        previous = float(part.UnitNAV.iloc[-1])
    return rows


def crisis_metrics(result):
    d = result.daily
    rows = []
    for label, start, end in [('dotcom_2000_2002', '2000-01-01', '2003-01-01'),
                              ('gfc_2007_2009', '2007-01-01', '2010-01-01'),
                              ('covid_2020', '2020-01-01', '2021-01-01'),
                              ('bear_2022', '2022-01-01', '2023-01-01')]:
        part = d.loc[(d.index >= start) & (d.index < end)]
        if part.empty:
            continue
        position = d.index.get_loc(part.index[0])
        prev = float(d.UnitNAV.iloc[position - 1]) if position else 1.
        curve = part.UnitNAV / prev
        dd = curve.to_numpy() / np.maximum.accumulate(np.r_[1., curve])[1:] - 1
        from .data import sessions
        full_dates = sessions(start, end)
        rows.append({'case_id': result.case_id, 'window': result.window, 'period': label,
                     'full_window_covered': part.index.equals(full_dates),
                     'actual_start': str(part.index[0].date()), 'actual_end': str(part.index[-1].date()),
                     'twr_return': float(curve.iloc[-1] - 1), 'window_drawdown': float(dd.min()),
                     'synthetic_held_return_days': int(part.SyntheticReturnExposure.gt(1e-12).sum())})
    return rows


def save_case(result, root):
    folder = Path(root) / result.case_id
    folder.mkdir()
    result.daily.to_csv(folder / 'daily.csv', index_label='Date', date_format='%Y-%m-%d', encoding='utf-8-sig')
    columns = ['execution_date', 'signal_date', 'execution_id', 'asset', 'side', 'notional', 'signed_notional',
               'cost', 'weight_before', 'weight_after', 'equity_before', 'equity_after', 'reason',
               'QQQ', 'MA200', 'RSI6', 'ROC35_pct', 'price_kind', 'price_index', 'return_source',
               'synthetic_return_on_execution_day']
    columns += sorted({k for t in result.trades for k in t} - set(columns))
    pd.DataFrame(result.trades, columns=columns).to_csv(folder / 'trades.csv', index=False, encoding='utf-8-sig')
    with (folder / 'events.jsonl').open('w', encoding='utf-8') as stream:
        for row in result.events:
            stream.write(json.dumps(clean(row), ensure_ascii=False, allow_nan=False) + '\n')
    write_json(folder / 'config.json', {'strategy': result.strategy, 'profile': result.profile,
                                      'window': result.window, **result.config})
    metrics = summarize(result)
    write_json(folder / 'metrics.json', metrics)
    return metrics, annual_returns(result), crisis_metrics(result)


def comparison_pairs(summary):
    """Apples-to-apples rule additions, never compare different time windows."""
    by_key = {(r['window'], r['strategy'], r['profile']): r for r in summary}
    rows = []
    pairs = [('hold_leveraged', 'ma200'), ('ma200', 'ma_roc'), ('ma_roc', 'bull_rsi'),
             ('bull_rsi', 'bull_bear_rsi'), ('user_rules', 'bull_bear_rsi'),
             ('user_rules', 'bear_roc_bridge')]
    for (window, strategy, profile), right in by_key.items():
        for left_s, right_s in pairs:
            left = by_key.get((window, left_s, profile))
            if strategy != right_s or left is None:
                continue
            rows.append({'window': window, 'profile': profile, 'base_strategy': left_s, 'new_strategy': right_s,
                         'cagr_change': right['twr_cagr'] - left['twr_cagr'] if right['twr_cagr'] is not None and left['twr_cagr'] is not None else None,
                         'drawdown_change': right['max_drawdown'] - left['max_drawdown'],
                         'final_equity_change': right['final_equity'] - left['final_equity'],
                         'trade_legs_change': right['trade_legs'] - left['trade_legs']})
    return rows


def write_overview(root, summary, annual, crisis, metadata):
    root = Path(root)
    pairs = comparison_pairs(summary)
    for name, rows in [('summary', summary), ('annual_returns', annual), ('crisis_windows', crisis), ('rule_contributions', pairs)]:
        pd.DataFrame(rows).to_csv(root / f'{name}.csv', index=False, encoding='utf-8-sig')
    warning = '研究回测，不是未来收益承诺。旧数据模式不等于行情认证；合成收益不是基金真实历史。'
    lines = ['# 策略对比结果', '', warning, '', '## 数据与执行口径', '',
             f"- 数据模式：`{metadata['source_mode']}`；结果由本地实际输入计算，文件哈希见 manifest。",
             f"- 信号价格口径：`{metadata.get('signal_basis', 'adjusted_return_index_legacy')}`；收益仍计入分红复投近似，不重复增加现金。",
             '- 信号滞后执行；日终定投；分数金额持仓；成本按买卖单边计算；不含税。',
             '- 末日按市值估值，不强制平仓；年化收益、回撤用剔除定投影响的单位净值。',
             '- long_history 的所有策略先以无息 USD 等待指标预热；没有回填 MA200。',
             '- post_tqqq：股票使用观测收益，SGOV 成立前为现金代理；all_observed 才是全部交易标的观测区间。',
             '- 不同历史区间不可直接比较终值；同一区间资金流完全一致。', '',
             '| 区间 | 策略 | 档位 | 终值 USD | 累计投入 | TWR年化 | 最大回撤 | 交易笔数 |',
             '|---|---|---|---:|---:|---:|---:|---:|']
    for r in summary:
        annualized = f"{r['twr_cagr']:.2%}" if r['twr_cagr'] is not None else 'N/A'
        lines.append(f"| {r['window']} | {r['strategy']} | {r['profile']} | {r['final_equity']:,.2f} | {r['contributed']:,.2f} | {annualized} | {r['max_drawdown']:.2%} | {r['trade_legs']} |")
    lines += ['', '## Review 入口', '',
              '`rule_contributions.csv` 比较每条规则的边际变化；回撤变化为正表示回撤变浅。',
              '`crisis_windows.csv` 的 full_window_covered=False 表示仅部分覆盖，不能称完整危机测试。',
              '每个 case 子目录含 daily.csv、trades.csv、events.jsonl、config.json、metrics.json。',
              '详细策略定义在仓库 docs/strategies/。结果没有进行参数择优或自动策略推荐。']
    (root / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    rows = []
    for r in summary:
        fields = [r['window'], r['strategy'], r['profile'], f"{r['final_equity']:,.2f}",
                  f"{r['contributed']:,.2f}", f"{r['twr_cagr']:.2%}" if r['twr_cagr'] is not None else 'N/A',
                  f"{r['max_drawdown']:.2%}", str(r['trade_legs']), str(r['synthetic_held_return_days'])]
        cells = ''.join('<td>' + html.escape(v) + '</td>' for v in fields)
        rows.append('<tr data-window="' + r['window'] + '">' + cells + '<td><a href="' + r['case_id'] + '/trades.csv">交易</a> / <a href="' + r['case_id'] + '/events.jsonl">事件</a></td></tr>')
    page = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>策略对比</title>
<style>body{font:15px/1.7 system-ui;margin:32px}table{border-collapse:collapse;width:100%}td,th{padding:7px;border-bottom:1px solid #ddd;text-align:right}td:nth-child(-n+3),th:nth-child(-n+3){text-align:left}th{position:sticky;top:0;background:#eee}select{padding:7px}small{display:block;margin:18px 0}</style>
<h1>策略对比 · 可审计研究</h1><p>WARN</p><p>同一区间比较收益、回撤与资金流；TWR 年化不把定投当利润。<a href="report.md">完整说明</a> · <a href="summary.csv">汇总 CSV</a> · <a href="rule_contributions.csv">规则贡献</a> · <a href="crisis_windows.csv">危机窗口</a></p>
<label>区间 <select id="filter"><option value="">全部</option>post_tqqq</option><option>long_history</option><option>all_observed</option></select></label>
<small>post_tqqq：股票观测、早期现金代理；long_history：上市前模型；all_observed：交易标的均已有观测，但数据仍需独立核验。</small>
<table><thead><tr><th>区间</th><th>策略</th><th>档位</th><th>终值 USD</th><th>累计投入</th><th>TWR 年化</th><th>最大回撤</th><th>交易笔数</th><th>合成持有日</th><th>日志</th></tr></thead><tbody>ROWS</tbody></table>
<script>document.querySelector('#filter').onchange=function(){document.querySelectorAll('tbody tr').forEach(r=>r.hidden=this.value!==''&&r.dataset.window!==this.value)}</script></html>'''
    page = page.replace('<option value="">全部</option>post_tqqq</option>', '<option value="">全部</option><option>post_tqqq</option>')
    (root / 'report.html').write_text(page.replace('WARN', html.escape(warning)).replace('ROWS', '\n'.join(rows)), encoding='utf-8')
