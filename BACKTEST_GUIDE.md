# 策略对比程序：启动、口径与 Review

本工具只做研究，不连接券商、不下真实订单，不自动合并 main、不创建 PR。
默认初始资金 **80,000 美元**，之后每月 **1,500 美元**。在相同数据、时序、资金流和成本下，对比基础策略及逐条增加的规则。**没有自动寻找最佳参数。**

## 1. 使用现有仓库数据，直接离线运行

在本仓库目录运行（Windows PowerShell 和 bash 都可以）：

```bash
git fetch origin
git switch feat/research-preinception-data
git pull --ff-only
python -m pip install -r requirements-backtest.txt
python run_comparison.py --accept-legacy-adjusted
```

推荐 Python 3.11/3.12。Windows 多 Python 环境可将 `python` 换成 `py -3.11`。
安装依赖需要网络；安装后，上述对比命令**不下载行情**，使用 `data/raw/*_raw.csv`。

`--accept-legacy-adjusted` 的含义是：你明确接受旧 CSV 的 Close 按调整价使用的研究假设。原文件缺少独立 Adj Close/公司行为/完整下载元数据，因此**不是经过认证的真实行情**。这个开关不跳过重复日期、缺失交易日、非正价格等结构校验，也不修改原文件。标准 `get_data.py` 的严格读取入口保持不变，不会把这些旧文件自动认证。

**不使用旧的 `*_synthetic_daily.csv`**：杠杆融资、现金计息、代理衔接在新适配层按明确假设重建。VIX 不在本策略中使用，不会为本次回测依赖它的旧 CSV。

默认运行 75 个组合：每个区间 1 个 QQQ 基准 + 6 个其他策略 × 4 个杠杆档位，三个区间。结果写入独立批次：

```text
results/comparisons/<run_id>/
  report.html              # 浏览器直接打开，不需要启动服务
  report.md                # 可读汇总表
  summary.csv              # 同区间收益、回撤、交易等对比
  rule_contributions.csv   # A→B→C→D 逐条增加规则的变化
  crisis_windows.csv      # 2000–2002、2007–2009、2020、2022
  annual_returns.csv      # 每年 TWR 收益
  input_panel.csv         # 实际使用的收益、指标、来源与模拟标记
  data_audit.json          # 原输入文件哈希、日期覆盖、模型参数、利率选择
  manifest.json           # 代码哈希、版本、参数、状态、输出哈希
  run.log
  <window>__<strategy>__<profile>/
    daily.csv             # 每日账户金额、贡献本金、单位净值、持仓、敞口
    trades.csv            # 每条真实换仓腿：买卖、金额、成本、信号与执行日期
    events.jsonl          # 状态改变、定投、抄底、退出、跳过原因等
    config.json
    metrics.json
```

终端最后输出 `RESULT_DIR=...`。出现 FAILED/非零退出码时，不得把部分结果当成完整比较。每次生成新目录，不会偷偷读上一次成功结果。

## 2. 三个区间，避免把代理数据叫成真实基金历史

| --windows | 起点 | 数据口径 |
|---|---|---|
| `post_tqqq` | TQQQ、QLD、QQQ、VTV 共同已有实际观测价格的首日 | 股票只赚取观测收益；早期 SGOV 缺历史时使用现金代理 |
| `long_history` | QQQ 现有文件首日（仓库为 1999-03-10） | 晚上市标的上市前模型；上市后观测收益 |
| `all_observed` | 上述标的加 SGOV 共同已有观测价格的首日 | 实际持有的所有交易标的收益均有观测，不赚取上市前合成收益 |

**成立日不一定等于供应商第一条交易记录。** 以文件实际观测起点和日志为准；例如仓库 TQQQ 文件起点是 2010-02-11。原始观测值仍可能存在供应商错误，不能因叫 `all_observed` 就视为独立核验。

指标在完整 QQQ/VTV 历史上先计算，然后截取交易窗口，避免 2010/2020 起点重新损失 200 天预热。

`long_history` 从 QQQ 首日起记录本金和定投；没有 MA200 时所有版本都先持有 **无息 USD**。第一笔策略配置在首个完整指标日后的下一交易日收盘执行。**不是从 1999-03-10 就拥有一个虚构的 MA200。** 同起点基准也采用此共同预热约束；它们是公平可比较的基准，而不是“上市首日立即买入”的另一个实验。

`--end` 不含当天。不指定时采用所有所需价格文件中最早的末日作为共同截止，明确记录在审计文件；需要固定批次时显式指定，例如 `--end 2026-08-01`。显式请求区间内缺价会失败，不会自动前移截止。

## 3. 策略与杠杆档位

每个策略都有独立 MD，见 [docs/strategies/README.md](docs/strategies/README.md)。

| 策略名 | 内容 |
|---|---|
| `hold_qqq` | 70% QQQ + 30% SGOV 基准 |
| `hold_leveraged` | 固定进攻配置，不择时 |
| `ma200` | A：QQQ > MA200 持有进攻配置，否则防守 |
| `ma_roc` | B：A + 比值 VTV/QQQ 的 ROC35 切换杠杆 |
| `bull_rsi` | C：B + 有上限、带退出的牛市 RSI 抄底 |
| `bull_bear_rsi` | D：C + 同样有约束的熊市 QQQ 抄底 |
| `user_rules` | 原始描述的明确解释版，保留牛市 5% SGOV / 熊市 5% 净值的不同口径 |

| --profiles | 进攻配置 | 近似初始日度指数敞口 | 作用 |
|---|---|---:|---|
| `tqqq70` | 70% TQQQ + 30% SGOV | 2.1x | 原进攻配置 |
| `qld70` | 70% QLD + 30% SGOV | 1.4x | 同持仓比例替换 |
| `qld100` | 100% QLD | 2.0x | 接近 2.1x 的无额外借款对照，但没有现金储备 |
| `tqqq46` | 46 2/3% TQQQ + 53 1/3% SGOV | 1.4x | 与 qld70 近似相同初始日度敞口 |

QLD 每日目标为2倍、TQQQ为3倍；它们不是长期收益固定乘数。**105% QLD 才达到约2.1倍，但需要融资，本程序没有暗中引入该方案。** 基准与 A 使用档位进攻比例；B/C/D/原始版在去杠杆牛市统一为70% QQQ+30% SGOV，熊市基础为100% SGOV。

`qld100` 进攻时通常没有现金可抄底，程序会记录 `DIP_SKIPPED_NO_CASH_OR_CAP`，不是悄悄借款。持仓在两次再平衡之间会漂移，实际敞口逐日记录，而不是每天重设基金组合权重。

## 4. 明确的默认交易约定

- QQQ/VTV 均按调整后收盘收益口径。`ROC35 = ((VTV/QQQ)_t / (VTV/QQQ)_(t-35) - 1) * 100`，不是两个 ROC 相除。RSI 使用 Wilder 初始简单平均及递推。
- 当天收盘完成指标，下一交易日收盘执行。`--signal-lag 2` 可测试更慢执行。买入当日不会赚取此前那段 close-to-close 收益；末日新信号不会假装成交到数据范围外。
- 默认单边综合交易成本5个基点（0.05%）：卖和买分别计费，包含手续费/价差/滑点的简化假设。SGOV 也计费，从裸 USD 买入只计买腿。费用从账户扣除，不凭空增加现金。
- 以分数金额/收益指数记账，不是实际美元股价或真实整数股数。没有税、汇率、保证金、券商结算限制、盘中止损、限价单、成交量容量约束。调整价内含的分红不再额外发放。
- 每月第一个交易日收盘注入1,500美元；初始月份不再额外注入。新资金不赚该日已经发生的收益。默认该日按目标比例再平衡，状态切换也再平衡。
- `--rebalance state_only` 禁止常规月度卖出再平衡；新增 USD 只向低配资产分配，状态变化仍按目标重设。持有基准在此设置下也仅用新增资金调整。
- 价格等于 MA 时保留前一趋势状态；ROC 等于0保留前一档位。无前一状态时保守使用熊市/去杠杆档位。默认无缓冲、单日确认；可通过配置启用缓冲与多日确认，但没有自动寻优。
- C/D 的抄底：RSI 从 >=21 跌至 <21，且已重新武装，才触发一次；RSI恢复 >=30 才允许新的独立信号。每次最多账户净值5%，全部战术仓合计在买入时不超过净值10%，现金不足按可用现金减量。
- C/D 牛市抄底遵守 ROC：允许杠杆买本档位ETF，否则买QQQ；D 熊市只买QQQ。C 不在熊市抄底。
- C/D 战术仓：RSI>=50的已知信号，或持有10个交易区间，于执行日退出到SGOV；状态改变时先重设战术仓，再做基础配置。退出后不必等 MA200 才恢复现金。
- 状态切换当天禁止新抄底；该次超卖机会被消费，不在持续低 RSI 的下一天补买。普通月度再平衡不取消战术仓；底仓按净值目标计算、战术仓额外占用SGOV。战术仓大幅上涨导致底仓放不下时先缩减底仓，不借款。
- 原始解释版存在不同约定，特别是强制首次进攻建仓、无10日/RSI50退出、无10%累计抄底上限；详见其独立文档。没有称其为用户未明确规则的唯一正确实现。

## 5. 常用命令

先看全部可选项：

```bash
python run_comparison.py --list
python run_comparison.py --help
```

只比较 TQQQ 上市后，TQQQ70 / QLD70 下的 A/B/C/D：

```bash
python run_comparison.py --accept-legacy-adjusted --windows post_tqqq --strategies ma200 ma_roc bull_rsi bull_bear_rsi --profiles tqqq70 qld70
```

长历史，原始版对比改进版，并打印所有关键事件：

```bash
python run_comparison.py --accept-legacy-adjusted --windows long_history --strategies user_rules bull_bear_rsi --profiles tqqq70 qld70 --verbose-trades
```

单独测试更高成本与两天确认：

```bash
python run_comparison.py --accept-legacy-adjusted --cost-bps 10 --confirm-days 2
```

指定配置文件（CLI 显式参数优先）：

```bash
python run_comparison.py --accept-legacy-adjusted --config configs/comparison.example.json
```

代理/融资敏感性（只能改动上市前模型，不改上市后观测收益）：

```bash
python run_comparison.py --accept-legacy-adjusted --windows long_history --funding-spread 0.015
python run_comparison.py --accept-legacy-adjusted --windows long_history --value-proxy SPY
```

使用之前的严格研究批次而不是旧 CSV（先具备该批次；此路径不自动降级读取旧数据）：

```bash
python -m pip install -r requirements.txt
python prepare_research.py --end 2026-08-01
python run_comparison.py --source research --end 2026-08-01 --scenario base
```

该模式读取 research 批次自己的参数，不接受通过本程序重新指定融资/费率参数，需选择或重建批次。

核对生成报告没有被修改：

```bash
python run_comparison.py --verify-run results/comparisons/实际批次目录名
```

这是输出完整性校验，不是数字签名认证；手工同时修改报告和 manifest 并非安全可信链。原 CSV 原地改变后，旧结果仍保留旧哈希，必须重跑以取得新结果。

## 6. 怎样 Review

先比较同一个 window 下的 `summary.csv`，不要把1999开始与2010开始的最终金额直接排名。关注 TWR年化、最大回撤、恢复日期、最长水下时间、XIRR、交易腿数和费用；账户终值与累计投入并列。

`rule_contributions.csv` 将固定进攻→A、A→B、B→C、C→D、原始版→D 配对。收益变化为正是提高；回撤变化为正是回撤变浅。它不是统计显著性检验，也不是策略推荐。

再看 `crisis_windows.csv`：窗口是固定完整年度范围而非事后挑峰谷，`full_window_covered=False` 会明确说明只覆盖了一部分。回撤是该窗口相对其起点/期内高水位的回撤，不是把整个历史最大回撤套给这个危机。

最后在 `events.jsonl` 搜索 `STATE_CHANGE`、`BULL_DIP_BUY`、`BEAR_DIP_BUY`、`TACTICAL_*EXIT`，到 `trades.csv` 按同一 execution_id 对照具体买卖金额。每一腿均有 signal_date、execution_date、MA200、RSI6、ROC35、前后权重、成本和数据来源；同资产在战术仓/底仓间重分类不会制造不必要的买卖手续费。

## 7. 模拟及风险边界

离线模式用 QQQ 调整后收益构造每日2倍/3倍复利，扣借入敞口的滞后 IRX 短债收益近似及融资利差，另扣基金费。IRX 是短债贴现收益率，不是基金真实融资利率，更不是股票价格。利息跨周末按日历天累计。默认91天票据与年费参数是情景假设，尚未校准；QQQ 自身费用及跟踪差异也使模型不是纳指衍生品的精确复制。

SGOV 早期为同一利率的现金持有近似，后接 BIL；VTV 早期为 VIVAX（可用SPY做敏感性）。上市首条观测日的连接收益标记 transition，第二条开始使用真实观测之间的收益率，不倒锚到未来上市价格。已上市后缺行情直接报错；没有用代理修补真实缺价。

缺少最初滞后利率时不假设零利率或偷看未来：初始模型价格可以暂不可用，所有策略在预热阶段持有USD。模型收益<=-100%时失败，不能通过截断变成永不破产的ETF。

数据来源接口参考：
- [ProShares QLD](https://www.proshares.com/our-etfs/leveraged-and-inverse/qld)、[TQQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq)：每日杠杆目标和多日偏离。
- [iShares SGOV](https://www.ishares.com/us/products/314116/ishares-0-3-month-treasury-bond-etf)：基金与短债范围。
- [pandas pct_change](https://pandas.pydata.org/docs/reference/api/pandas.Series.pct_change.html)：程序显式禁用收益率计算的缺值填充。

## 8. 测试与仓库自动运行

```bash
python -m unittest discover -s tests -p test_comparison.py -v
```

测试全部使用标记为人造的样例。要跑原有全部测试，先安装原 requirements.txt，再运行 `python -m unittest discover -s tests -v`。本地测试通过不等于供应商价格已独立核验。

附带 `.github/workflows/compare.yml`：本功能分支的回测相关代码 push 时在 GitHub Actions 中运行测试，并使用仓库现有 CSV 执行对比；结果与失败日志保存在 artifact 中，保留14天。权限仅 contents:read，不提交结果、不修改main、不进行券商操作。是否执行成功以 Actions 的实际状态为准；该工作流也支持 workflow_dispatch（GitHub 手动触发入口可能要求文件先存在于默认分支）。本地启动不依赖 Actions。
