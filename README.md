# back_trader_test

可审计的日线研究回测：初始80,000美元、每月1,500美元，对比 QQQ/杠杆ETF 趋势、相对强弱和 RSI 抄底策略。

## 最新澄清：均线下ROC转负，先买QQQ

新版策略名 **`bear_roc_bridge`**；旧 `user_rules` 保留作对照，不会悄悄改变旧75组默认实验。
信号价和含分红复投收益已分开；新策略必须显式选择独立信号数据。

```bash
python -m pip install -r requirements.txt -r requirements-backtest.txt
python prepare_signal_prices.py --end 2026-08-01
python run_comparison.py --accept-legacy-adjusted --signal-source split_close --end 2026-08-01 --strategies bear_roc_bridge --profiles tqqq70 --windows post_tqqq --verbose-trades
```

默认过渡仓70% QQQ，直到恢复MA200再按ROC决定是否切70% TQQQ。4月1日收盘信号对应下一交易日收盘执行，不进行同日未来信息成交。
完整规则和边界：[新版策略说明](docs/strategies/bear_roc_bridge.md)。单日TradingView截图校验通过不代表全历史同源；行情下载或核验失败会报错，不使用旧复权信号偷偷回退。

## 直接运行现有数据的策略对比

Python 3.11/3.12，在本仓库目录：

```bash
python -m pip install -r requirements-backtest.txt
python run_comparison.py --accept-legacy-adjusted
```

结果在 `results/comparisons/<run_id>/report.html`，浏览器直接打开。所有策略有独立说明，每次交易和状态变化都有日志；原 CSV 不会被修改。

**重要：** `--accept-legacy-adjusted` 明确接受旧 CSV 的 Close 按调整价使用的假设，不是认证旧数据。程序不使用旧 `*_synthetic_daily.csv`，会重建所需上市前模型；有数据缺口则失败，不悄悄补造已上市后的行情。

完整启动、参数、数据口径、输出和 Review 方法：[BACKTEST_GUIDE.md](BACKTEST_GUIDE.md)。每种策略说明：[docs/strategies/README.md](docs/strategies/README.md)。

默认75组对比：7种规则、4种杠杆档位、3个历史窗口（固定QQQ基准不重复4次）。

| 窗口 | 用途 |
|---|---|
| post_tqqq | TQQQ等股票已有观测，SGOV早期使用现金代理 |
| long_history | 从QQQ文件起点开始，晚成立基金使用明确标记的模拟 |
| all_observed | 全部实际交易标的均有观测数据后的对照 |

QLD 包含70%和100%配置；46 2/3% TQQQ 与70% QLD 都约为1.4倍初始日度指数敞口。没有使用105% QLD或额外券商融资。

```bash
python run_comparison.py --list
python run_comparison.py --accept-legacy-adjusted --windows post_tqqq --profiles tqqq70 qld70
python run_comparison.py --accept-legacy-adjusted --strategies user_rules bull_bear_rsi --verbose-trades
python -m unittest discover -s tests -p test_comparison.py -v
```

## 保留的严格数据准备流程

`get_data.py` 继续只处理观测数据，`prepare_research.py` 在经过来源记录/结构检查的批次上增加独立模拟层；均不把供应商数据称为独立认证行情。

```bash
python -m pip install -r requirements.txt
python prepare_research.py --end 2026-08-01
python run_comparison.py --source research --end 2026-08-01
```

这一条路径需要数据源网络可用并且批次校验通过，失败不自动退回旧 CSV。历史数据准备方法见 [RESEARCH_DATA.md](RESEARCH_DATA.md)。本次新增回测前的观测流程原说明保存在 [OBSERVED_DATA.md](OBSERVED_DATA.md) 供追溯；其中“尚无回测引擎”描述的是当时状态，当前入口以本页和 BACKTEST_GUIDE 为准。

## 审计与限制

- 日终信号至少滞后一个交易日执行，不制造开盘价/盘中成交。
- 剔除定投影响的单位净值计算 TWR 与回撤，同时报告账户金额、累计投入和 XIRR。
- 模拟是反事实研究，不是基金上市前真实历史；已观测也不代表供应商从无错误。
- 费用、融资、代理选择为固定研究假设；没有参数自动寻优、税务处理或真实券商执行。
- 运行与输入/输出哈希、指标、逐笔交易及跳过操作的原因均保留。Tests 使用人造样例，不是收益结论。
