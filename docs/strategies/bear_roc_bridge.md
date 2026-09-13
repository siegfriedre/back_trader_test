# bear_roc_bridge：熊市 ROC 转负，先以 QQQ 等待 MA200 恢复

本版本实现用户的新澄清，不是给旧回测某一天打补丁。旧 `user_rules` 和 A/B/C/D 保持原定义，以便分开比较「数据口径变化」和「交易规则变化」。

## 状态与仓位

| 状态 | 条件/进入方式 | 基础仓位 |
|---|---|---|
| WAIT | 指标尚未预热 | 无息 USD |
| BEAR | QQQ低于MA200，尚无有效的后续ROC下穿 | 100% SGOV；仍可按原解释版RSI规则做5% QQQ试探 |
| BEAR_QQQ | 已在均线下方，ROC从不低于0变为负数 | 默认70% QQQ＋30% SGOV |
| AGGRESSIVE | 重新高于MA200且ROC为负 | 本档位进攻配置；tqqq70为70% TQQQ＋30% SGOV |
| DEFENSIVE | 高于MA200且ROC为正 | 70% QQQ＋30% SGOV |

**70%过渡QQQ仓位是明确的实现假设。** 用户最新消息没有另报过渡仓位，采用其原70%股票底仓；`--bridge-weight 0.70` 可改。它是总目标仓位，不是在已有QQQ上再加70%。现有5% RSI试探仓会归入这70%，并在日志中记录分类重置和实际净交易。

## 事件优先级和边界

- 进入需要新的下穿事件：当前ROC<0且前一日ROC>=0。首次看到负值、连续负值都不是每天重复入场。
- 从牛市新跌破MA200时，趋势退出优先；即便当天ROC也下穿，仍先清股票到SGOV，等待之后新的ROC下穿事件。
- 进入BEAR_QQQ后，一直以QQQ等待MA200恢复；期间ROC反弹回正也不自动撤掉过渡仓。这是对「直到上穿MA200」的明确解释。
- 收复MA200时再读取ROC：仍负则切入杠杆档；若已为正，按此前牛市规则维持70% QQQ，而非盲目加杠杆。
- BEAR_QQQ期间不再额外叠加RSI抄底，更不会在均线下买TQQQ。跳过操作记为 `DIP_SKIPPED_BRIDGE_BASE`。
- 桥接状态之外保留原 `user_rules` 的RSI解释：牛市用SGOV余额5%买所选杠杆ETF，熊市用净值5%买QQQ；同一超卖过程仅一次，RSI>=30重新允许触发；无10日/RSI50强制退出、无额外10%累计上限。
- 新版本首次配置服从当时状态，不继承旧版「先强行买一次TQQQ，次日再纠正」的初始操作。
- 等于MA或ROC零轴保留原趋势/档位。默认无缓冲、单日确认；已有 `trend_band_bps`、`roc_band_pct`、`confirm_days` 仍可显式配置。非零ROC缓冲时，桥接触发线为负缓冲线，而不是严格零轴；核对原TradingView请保持默认0。
- 状态切换先重设战术仓并再平衡；常规月度定投和再平衡保持不变。

## 信号与收益分开

必须使用 `--signal-source split_close`，拒绝旧复权收益指数充当信号报价。QQQ/VTV信号来自单独下载、明确关闭分红自动调整的Close；QQQ MA200和RSI6也用这套信号价。收益仍用原含分红复投近似的收益序列，不重复发放股息、也不把股息当新增本金。

股票Close由Yahoo返回，**不是TradingView授权行情接口**。脚本用用户截图中2026-04-01的QQQ Close=584.31、ROC35=-0.0089及下穿标签做单日验收。该校验不提供MA/RSI截图值、不证明全历史一致、不参与生成交易信号；不匹配会保存差异并阻止发布结果。

VTV上市前的信号采用VIVAX不分红调整Close收益代理，并逐日标记。其显示比例只乘固定常数，不改变比值ROC。模拟不等于当年真实VTV历史。

## 执行时间与日志

严格保留已知信号延迟：4月1日收盘形成下穿信号，默认4月2日收盘才买QQQ；不能用完整收盘数据又假设同日无延迟成交。若之后收复MA，则仍在下一交易日收盘换仓。没有在代码中写死这些交易日期。

- `BEAR_ROC_QQQ_ENTRY` / `BEAR_ROC_QQQ_REBALANCE`：进入QQQ过渡仓。
- `BEAR_ROC_QQQ_EXIT` / `BRIDGE_TO_BULL_REBALANCE`：收复MA后退出过渡状态。
- `events.jsonl` 和 `trades.csv` 记录信号日、执行日、QQQ/VTV、MA、RSI、ROC、比值、35期参照日及比值、价格来源与仓位。
- `signal_comparison.csv`：旧调整价信号与新信号每日差异。
- `tradingview_reference_check.json`：截图单日核验，绝不通过修改价格或日期强行通过。

## 启动

```bash
python -m pip install -r requirements.txt -r requirements-backtest.txt
python prepare_signal_prices.py --end 2026-08-01
python run_comparison.py --accept-legacy-adjusted --signal-source split_close --end 2026-08-01 --strategies bear_roc_bridge --profiles tqqq70 --windows post_tqqq --verbose-trades
```

对比旧规则和新规则、TQQQ和QLD（同一新信号价）：

```bash
python run_comparison.py --accept-legacy-adjusted --signal-source split_close --end 2026-08-01 --strategies user_rules bear_roc_bridge --profiles tqqq70 qld70
```

省略 `--windows` 会跑三个窗口。结果仍在独立 `results/comparisons/<run_id>/`。原始CSV和过去的结果不覆盖。需要联网的只有依赖安装和独立信号价准备；准备成功后同一截止日可重复离线回测。
