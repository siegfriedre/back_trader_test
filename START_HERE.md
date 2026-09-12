# 先从这里启动

代码位于 `feat/research-preinception-data`，main保持不变；没有重新打开旧PR。

## 最少操作

在本地仓库根目录运行：

```bash
git fetch origin
git switch feat/research-preinception-data
git pull --ff-only origin feat/research-preinception-data
python -m pip install -r requirements-backtest.txt
python run_comparison.py --accept-legacy-adjusted
```

推荐Python 3.11/3.12。已有虚拟环境时用环境内的Python。Windows可执行 `py -3.11 -m venv .venv`，再把上述python换为 `.\.venv\Scripts\python.exe`，不用激活脚本。

安装依赖后，回测本身离线读取仓库现有 `data/raw/`。末日采用共同可用截止，或用 `--end 2026-08-01` 固定到2026-07-31。`--accept-legacy-adjusted` 是接受旧Close复权口径的研究假设，不是独立行情认证。

默认75组：7种策略 × 4种进攻配置 × 3个历史窗口，QQQ基准在每个窗口只跑一次。80,000美元初始，每月1,500美元；单边综合成本默认5bp。最终采用仓库Config中的这些默认值，不使用草案中其他成本假设。

终端末尾会输出 `RESULT_DIR=...`。直接用浏览器打开该目录的 `report.html`，无需启动Web服务。`report.md`和`summary.csv`是可读/可筛选的汇总。

## 两条最常用的筛选命令

只比较TQQQ上市后，TQQQ70和QLD70的A/B/C/D：

```bash
python run_comparison.py --accept-legacy-adjusted --windows post_tqqq --strategies ma200 ma_roc bull_rsi bull_bear_rsi --profiles tqqq70 qld70
```

长历史，原意解释版对比改进版，打印操作事件：

```bash
python run_comparison.py --accept-legacy-adjusted --windows long_history --strategies user_rules bull_bear_rsi --profiles tqqq70 qld70 --verbose-trades
```

其他可选进攻配置：`qld100`为100%QLD、约2倍敞口且没有SGOV储备；`tqqq46`为46又2/3%TQQQ，与70%QLD同为约1.4倍日度敞口。70%TQQQ约2.1倍，70%QLD约1.4倍，不能称为同风险替代。精确2.1倍的105%QLD需要融资，程序没有这样做。

`post_tqqq`早期SGOV仍是代理；`long_history`从QQQ文件起点记录资金，缺MA200时所有策略先在无息USD等待共同预热；`all_observed`才是全部持仓标的都有观测的窗口。信号收盘后形成，下一交易日收盘执行。

## 文档与Review

- [完整参数与执行口径](BACKTEST_GUIDE.md)
- [每种策略的独立MD](docs/strategies/README.md)
- [查看交易日志并关联信号](docs/EXECUTION_REVIEW.md)
- [已核实的GitHub完整运行记录](docs/VERIFIED_RUN_20260912.md)

每个case目录中有 `daily.csv`、`trades.csv`、`events.jsonl`、`config.json`、`metrics.json`；根目录 `rule_contributions.csv` 对比逐条增加规则的影响，`crisis_windows.csv`列出危机窗口。收益与回撤使用剔除定投影响的单位净值，期末金额不能与初始8万美元直接相除当成投资收益。
