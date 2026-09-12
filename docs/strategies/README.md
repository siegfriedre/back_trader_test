# 独立策略说明

共同执行约定和命令见 [BACKTEST_GUIDE.md](../../BACKTEST_GUIDE.md)。这些版本没有通过历史结果自动择优。

| 策略参数名 | 说明 |
|---|---|
| `hold_qqq` | [固定 QQQ 基准](hold_qqq.md) |
| `hold_leveraged` | [固定进攻基准](hold_leveraged.md) |
| `ma200` | [A：仅 MA200](ma200.md) |
| `ma_roc` | [B：MA200 + ROC35](ma_roc.md) |
| `bull_rsi` | [C：B + 牛市 RSI 抄底](bull_rsi.md) |
| `bull_bear_rsi` | [D：C + 熊市 RSI 抄底](bull_bear_rsi.md) |
| `user_rules` | [原始描述的明确解释版](user_rules.md) |

`user_rules` 不是声称用户未说清的规则只有这一种解释；其补充约定已经显式列出。C/D是建议的有约束版本，关键区别可通过配置和日志逐项复查。
