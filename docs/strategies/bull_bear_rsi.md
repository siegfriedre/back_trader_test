# D：C + 熊市 RSI 抄底

参数名：`bull_bear_rsi`

## 目标

通过D−C检验熊市抄底的贡献；另与user_rules比较更明确的退出和累计上限是否有价值。

## 基础与牛市规则

基础配置与B相同，牛市抄底与C相同：允许杠杆档买profileETF，否则买QQQ；熊市基础100%SGOV。

## 熊市战术规则

RSI6从>=21跌到<21且已经重新武装时，使用最多账户净值5%买QQQ，不买杠杆ETF。RSI>=30重新武装；一次持续超卖只买一次。牛熊战术仓共用买入时净值10%的累计上限，现金不足减量，不借款。

每笔在已知RSI>=50或持有10个交易区间时退出到SGOV。趋势/杠杆档位发生变化时重设战术仓并进行基础再平衡；转熊当天以退出为先，不同时新开抄底。被跳过的超卖信号会被消费，持续低RSI不在次日补买。

## 资金与成交

初始80,000美元；次月起每月首交易日收盘追加1,500美元。信号至少下一交易日收盘执行。默认月度再平衡保留战术仓；state_only时只用新增现金补低配，状态变化仍重设比例。按实际净买卖单边5bps扣成本，预热期间无息USD，不含税与真实券商约束。

## Review

```bash
python run_comparison.py --accept-legacy-adjusted --strategies bull_rsi bull_bear_rsi user_rules --profiles tqqq70 qld70 --verbose-trades
```

重点查BEAR_DIP_BUY、TACTICAL_*EXIT和状态转换。D−C回撤变化为正代表回撤变浅，不代表统计显著。危机窗口full_window_covered=False不能当作完整危机测试。实际数据与上市前模拟必须分开解释，详见 [运行指南](../../BACKTEST_GUIDE.md)。
