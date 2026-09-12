# 固定 QQQ 基准

参数名：`hold_qqq`

## 目标

判断复杂策略是否优于不用杠杆的固定组合。

## 基础配置

基础仓始终为70% QQQ、30% SGOV。

本策略不依赖所选杠杆profile，每个window只运行一次，case目录沿用首个profile名称。

## 信号和操作

不使用 MA200、ROC、RSI 进行择时，但为了对照一致性，也等待共同指标预热完成后才首次建仓。没有战术仓。

## 定投与再平衡

初始80,000美元，每月首个交易日收盘定投1,500美元，初始月不重复追加。默认月度恢复70/30；`--rebalance state_only` 时只使用新增资金补低配，不主动卖出。

## 执行和Review

共同预热期间为无息USD。至少滞后一个交易日收盘成交，不赚取买入前已经发生的当日收益。单边成本默认5bps，分数金额/调整收益指数记账，不是实际整数股交易。所有事件、交易、成本见case目录的events.jsonl、trades.csv和daily.csv。

```bash
python run_comparison.py --accept-legacy-adjusted --strategies hold_qqq
```

此基准不是“1999首日立即买入”的单独实验。post_tqqq仍含早期现金代理；long_history含SGOV模拟；all_observed也不是供应商数据认证。共同口径见 [运行指南](../../BACKTEST_GUIDE.md)。
