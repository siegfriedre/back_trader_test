# 固定进攻基准

参数名：`hold_leveraged`

## 目标

与A比较MA200退出对同一杠杆组合的贡献。

## 配置

始终持有profile的进攻配置，不因熊市清仓：tqqq70为70%TQQQ/30%SGOV，qld70为70%QLD/30%SGOV，qld100为100%QLD，tqqq46为46 2/3%TQQQ/53 1/3%SGOV。没有额外借款，没有抄底仓。

## 信号和操作

不择时，但采用与其他版本相同的完整指标预热。初始80,000美元；每月首个交易日收盘注入1,500美元，初始月不重复。默认月度恢复目标；state_only模式只用新增资金补低配。期间比例可以漂移，不是每日重设。

## 执行和Review

至少滞后一天收盘执行，共同预热期间为无息USD。买卖单边默认5bps，不含税。复权收益不重复派发股息；金额持仓不是实际整数股。daily.csv记录实际敞口，trades.csv记录净买卖腿，events.jsonl记录定投和再平衡。

```bash
python run_comparison.py --accept-legacy-adjusted --strategies hold_leveraged --profiles tqqq70 qld70 qld100 tqqq46
```

QLD100没有现金储备，不应与70%TQQQ的风险管理视为完全等价。基金每日杠杆不意味着长期收益固定倍数。历史窗口、数据和执行限制见 [运行指南](../../BACKTEST_GUIDE.md)。
