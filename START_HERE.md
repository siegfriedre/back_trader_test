# 最新启动入口：熊市ROC下穿，先以QQQ等待MA200

在仓库目录执行（Python 3.11/3.12）：

```bash
git fetch origin
git switch feat/research-preinception-data
git pull --ff-only
python -m pip install -r requirements.txt -r requirements-backtest.txt
python prepare_signal_prices.py --end 2026-08-01
python run_comparison.py --accept-legacy-adjusted --signal-source split_close --end 2026-08-01 --strategies bear_roc_bridge --profiles tqqq70 --windows post_tqqq --verbose-trades
```

最后一条命令不再下载行情。独立信号快照准备成功后，可以反复使用；任何下载/校验失败都不会偷偷改用旧复权信号。

打开终端 `RESULT_DIR=...` 对应目录的 `report.html`。重点核对：

- `tradingview_reference_check.json`：用户截图中2026-04-01的QQQ Close=584.31、ROC35=-0.0089和下穿标签；仅此单日核验，不认证全历史。
- `signal_comparison.csv`：旧复权信号与新信号的每日差异。
- 每个策略子目录里的 `trades.csv`、`events.jsonl`、`daily.csv`：实际信号日、执行日、持仓、指标和原因。

默认过渡仓为70% QQQ＋30% SGOV，可通过 `--bridge-weight 0.70` 修改。若4月1日收盘形成信号，默认4月2日收盘执行；不会用完整收盘数据假装同日无延迟成交。

对比旧解释版和最新澄清版、TQQQ和QLD，跑全部三个历史窗口：

```bash
python run_comparison.py --accept-legacy-adjusted --signal-source split_close --end 2026-08-01 --strategies user_rules bear_roc_bridge --profiles tqqq70 qld70
```

新策略：[bear_roc_bridge说明](docs/strategies/bear_roc_bridge.md)。旧75组默认实验保留，旧完整操作指南仍见 [BACKTEST_GUIDE.md](BACKTEST_GUIDE.md)。

信号与分红复投收益分开；收益沿用原序列，不重复发放现金股息。原 `data/raw` 缺少完整来源/复权元数据，因此仍需显式接受旧调整价假设。历史模拟和Yahoo行情都不能称为TradingView同源认证数据。
