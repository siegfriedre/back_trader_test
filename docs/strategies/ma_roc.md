# B：MA200 + ROC35

参数名：`ma_roc`

## 目标

通过B−A检验相对强弱是否改善杠杆使用，还是仅增加换仓。

## 状态表

| 状态 | 配置 |
|---|---|
| QQQ低于MA200 | 100%SGOV |
| QQQ高于MA200，ROC35<0 | profile进攻配置 |
| QQQ高于MA200，ROC35>0 | 70%QQQ、30%SGOV |

ROC35为 `((VTV/QQQ)_t/(VTV/QQQ)_(t-35)-1)*100`，不是两个ROC相除。ROC等于0保留旧档位，无旧档位时保守使用去杠杆档；QQQ等于MA保持此前趋势。默认无缓冲、一次确认，可通过配置测试缓冲/连续确认，没有自动调参。

## 资金、时序和Review

初始80,000美元、次月起每月首交易日收盘追加1,500美元；共同指标预热期间无息USD。信号至少下一交易日收盘执行，不使用当天尚不可知的指标成交。状态改变再平衡，默认月度也再平衡；state_only模式月度只分配新增资金。没有战术仓，单边成本默认5bps，不含税。

```bash
python run_comparison.py --accept-legacy-adjusted --strategies ma200 ma_roc --profiles tqqq70 qld70
```

在rule_contributions.csv看A→B，在events.jsonl核对AGGRESSIVE/DEFENSIVE/BEAR的转换。VTV早期代理能改变ROC正负，必须区分post_tqqq和long_history的证据强度。详细口径见 [运行指南](../../BACKTEST_GUIDE.md)。
