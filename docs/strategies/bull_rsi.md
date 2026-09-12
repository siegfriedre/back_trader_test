# C：B + 牛市 RSI 抄底

参数名：`bull_rsi`

## 目标

通过C−B检验牛市超卖加仓的边际收益、回撤和额外交易成本。

## 基础仓

与B完全相同：熊市100%SGOV；牛市ROC35<0用profile进攻配置，ROC35>0用70%QQQ/30%SGOV。profile进攻比例及信号定义见B和运行指南。

## 牛市战术仓

RSI6从>=21跌至<21且已经重新武装时，买入最多账户当时净值5%。恢复RSI>=30才允许下一个独立事件，不连续每日买。允许杠杆档买profile对应ETF，去杠杆档只买QQQ。

所有战术仓在买入时合计不超过净值10%；不足现金减量或跳过，不借款。QLD100进攻时没有现金，通常会记录DIP_SKIPPED_NO_CASH_OR_CAP。熊市不抄底。

每笔战术仓在已知RSI>=50或持有10个交易区间时退出到SGOV，状态切换时重设仓位。状态转换当天禁止新抄底且消费该次信号；不会在持续低RSI的下一日补买。

## 再平衡与执行

默认每月首交易日收盘注资1,500美元，初始80,000美元，初始月不额外注资。底仓按净值目标，战术仓额外占用SGOV；普通月度再平衡不抹掉战术仓。若战术仓涨得过大，缩减底仓以避免借款。信号至少滞后一个交易日收盘执行，费用默认买卖单边5bps，共同预热期间无息USD。

## Review

```bash
python run_comparison.py --accept-legacy-adjusted --strategies ma_roc bull_rsi --profiles tqqq70 qld70 --verbose-trades
```

查BULL_DIP_BUY、TACTICAL_RSI_EXIT、TACTICAL_TIME_EXIT及DIP_SKIPPED_*，按execution_id对应trades.csv。收益和回撤看单位净值，不混入定投。数据/窗口/税费限制见 [运行指南](../../BACKTEST_GUIDE.md)。
