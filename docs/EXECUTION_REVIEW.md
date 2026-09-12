# 如何Review关键操作

主入口仍然是 `run_comparison.py`；本次保留已经提交并通过完整CSV运行的实现，不增加另一套重复回测引擎。每种策略规则见 [strategies/README.md](strategies/README.md)。

## 查看原始输出

每次运行产生 `results/comparisons/<run_id>/`。首先打开 `report.html` 或 `summary.csv`，再进入具体case，例如：

```text
long_history__user_rules__tqqq70/
  daily.csv
  trades.csv
  events.jsonl
  config.json
  metrics.json
```

`events.jsonl`每行是一个JSON事件，包含event_id、信号日期、执行日期、MA200、RSI6和ROC35。`trades.csv`的execution_id引用对应事件，记录实际买卖的资产、金额、费用、成交前后权重和return_source。没有实际换仓的状态事件不应人为补一笔成交。

关键事件包括STATE_CHANGE、REGIME_REBALANCE、MONTHLY_CONTRIBUTION、BULL_DIP_BUY、BEAR_DIP_BUY、TACTICAL_RSI_EXIT、TACTICAL_TIME_EXIT，以及DIP_SKIPPED_*。`--verbose-trades`使回测时终端和run.log同步打印事件；不加该参数时events.jsonl仍完整保存。

## 不重跑回测，按日期/资产/事件筛选

新增 `review_events.py` 只用Python标准库，不下载数据、不修改报告、不下单。将下方RUN_ID替换成终端输出的批次名：

```bash
python review_events.py --run results/comparisons/RUN_ID --list-cases
python review_events.py --run results/comparisons/RUN_ID --case long_history__user_rules__tqqq70 --from-date 2008-09-01 --to-date 2008-10-31
python review_events.py --run results/comparisons/RUN_ID --case long_history__user_rules__tqqq70 --event DIP --asset TQQQ --limit 20
```

日期两端都包含，筛选的是执行日期；信号日期另外保留。event为不区分大小写的包含匹配。资产筛选匹配事件资产或该事件关联的任意交易腿，因此可用SGOV筛选为抄底提供资金的卖出。默认最多打印50个事件，`--limit 0`打印全部。

第一行显示本次case配置、匹配数量和打印数量；随后每行是一条原始事件，增加 `trades` 数组列出它对应的全部交易腿。没有实际交易则该数组为空。这避免只看买入信号而漏看同日卖出及成本。

查看前检查case的config.json、events.jsonl、trades.csv是否匹配已完成manifest中的SHA-256。损坏文件、失败批次、重复event_id、找不到事件的交易记录会报错。此检查不是行情真实性认证。完整输出目录校验仍使用原入口：

```bash
python run_comparison.py --verify-run results/comparisons/RUN_ID
```

## 推荐人工复核顺序

先看config.json冻结的规则，再挑一次跌破MA200、一笔ROC去杠杆、一笔牛市抄底、一笔熊市抄底和一次月初定投。核对信号日早于执行日；在input_panel.csv复算对应指标；确认trades中的买卖/费用与daily中的净值和持仓衔接；定投资金不得成为当日投资收益。

仅RSI的买入/退出是战术仓和SGOV之间的转移，不应偷偷将漂移底仓强制再平衡；状态切换和月度再平衡则按基础目标处理。100%QLD没有SGOV可用时，应看到DIP_SKIPPED_NO_CASH_OR_CAP而非负现金。

当前仓库回测实现及75组数据比较的核实记录见 [VERIFIED_RUN_20260912.md](VERIFIED_RUN_20260912.md)。本日志查看器另外有8项标准库测试，使用人造日志，不是行情证据。
