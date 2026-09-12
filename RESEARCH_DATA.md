# 上市前研究数据：互联网泡沫与 2008 年危机

## 目标与当前状态

这里研究的是：**今天选择的资产暴露和策略，放回过去的危机路径会怎样**，不是证明这些基金当年存在或可被买卖。先准备数据，不实现、选择或优化用户尚未提供的策略。

本功能叠加在 PR #1 的真实观测数据校验之上，保留 `get_data.py`、`load_verified_data()`、原始供应商表和失败阻断机制。旧的 `data/*_synthetic_daily.csv` 和 `data/raw/` 只用于审计，不会自动导入或认证。

本次已执行 31 项新增离线测试，全部通过；使用的是人造测试样例，不是行情。当前执行环境外网 DNS 失败，**尚未生成新的完整研究数据批次，也没有复跑 PR #1 的在线下载或完成股票行情第二来源核验**。代码完成与真实数据就绪是两件事。

## 运行

在包含 PR #1 的分支上，安装其 `requirements.txt`，然后运行：

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python prepare_research.py --end 2026-08-01
```

`--end` 不包含当天；此例截止 2026-07-31，与审查时仓库 QQQ 旧文件的末日一致。默认起点为 1999-03-10，保留全部 MA200 预热行。程序要求完整覆盖 2000—2002、2007—2009 两个检验窗口，并且截止日期足以取得所有晚成立基金的真实观测数据；本版不是按历史时点选取当时基金池的引擎。

默认重新下载并校验 10 只股票/ETF，再下载 BIL、VYM、VIVAX、FRED 利率以及 Cboe VIX。只有所有依赖成功，才发布研究批次。需要复用已经通过校验且起止日期完全一致的真实批次时：

```bash
python prepare_research.py --end 2026-08-01 --reuse-verified
```

复用仅针对真实股票/ETF 批次；补充代理、利率和 VIX 仍会重新下载并保存。任何错误返回非零退出码，`data/research/latest.json` 标记失败，读取器不自动退回旧版本。没有真实批次时，不能用旧 CSV 重命名替代。

## 资产与上市前方法

| 目标 | 上市前研究输入 | 边界 |
| --- | --- | --- |
| QQQ、SPY、KO | 从研究起点直接使用观测收益 | 不合成、不补上市后缺价 |
| QLD、TQQQ | QQQ 调整后日收益的 2 倍/3 倍，逐日复利并扣资金成本和费用假设 | QQQ 不是指数原始收益；这不是发行商 NAV 的精确复刻 |
| VOO | SPY 观测总回报近似 | 基金费用、跟踪差异未精确重建 |
| VTV | VIVAX 观测调整后 NAV 收益 | 价值风格代理，不宣称两者历年基准完全相同 |
| SCHD | 早期 VIVAX，VYM 有连续观测后改用 VYM | 不是 SCHD 指数重建；替代情景用 SPY |
| CGDV | 早期 VIVAX，VTV 有连续观测后改用 VTV | 主动管理基金不可重建当时不存在的持仓决策；替代情景用 SPY |
| SGOV | 早期 DTB3 短券利率 carry 模型，BIL 有连续观测后用 BIL | carry 不包含真实短债全部盯市波动、价差或历史持仓；不等同 SGOV |
| VIX | Cboe 官方历史指数水平 | 仅作信号；不当 ETF 买卖，不重置为 100 |

所有代理切换都需要新代理的两个连续观测收盘价。不会把中途断档误当成上市前缺失，也不以其他代理掩盖一个输入源的坏数据。

### 每日杠杆复利

模型采用：

```text
r_model[t] = L * r_QQQ_adjusted[t]
             - (L - 1) * (lagged_DFF[t] + funding_spread) * calendar_days[t] / 360
             - fund_fee * calendar_days[t] / 365
```

这是明确的研究近似。QQQ 自带的费用与跟踪差异未反向精确剔除；真实 ETF 的衍生品融资、再平衡、税费和跟踪误差也不可能全部由这一式子覆盖。**不另外扣固定“波动损耗”**：每日收益连乘已经产生路径依赖。若模型单日收益小于等于 -100%，直接报错并标记模型失效；不裁成 -99%、不倒推负净值，也不在基金真实上市时强行复活。

费用及融资利差只是预先指定的情景假设，不是历史费率数据，也不是用基金未来表现拟合出来的参数。基础情景：年基金费用 1%、融资利差 0.5%；高融资情景：融资利差 1.5%。这些情景不能视为真实成本的上下界，也不能保证高融资版本对任何择时策略都更保守。

### 现金与利率时间

DTB3 是年化贴现率百分数，不是价格。转为小数 `d`，假设短券到期天数 `T=91`，近似区间 carry 为：

```text
cash_return = d * calendar_days / (360 - d * T)
              - cash_fee * calendar_days / 365
```

现金费用假设为年化 0.1%。周末、假日、异常停市之间的日历天数都计入。缺失利率不填 0；允许按明确规则沿用先前利率，但超过 14 天拒绝。默认只使用区间开始日之前至少 2 个日历日的利率观测，并输出实际使用的观测日期。

固定 2 天是研究中的可用时间假设，不是已核验的发布时刻日历。FRED 下载是可能修订的历史序列，不是当时可见的 vintage；所有结果 `PointInTimeCertified=False`。这降低直接使用当日/未来利率的风险，不构成完全消除所有前视偏差的证明。

### 上市衔接与价格口径

股票/ETF 的 `ResearchClose` 从 100 向前逐日连乘，不用未来的上市价格给旧历史定标。它是**收益指数，不是美元/份价格**。

`ObservedAdjustedClose` 单独保留供应商的调整收盘价。首个观测日期之前用模型；从模拟期接入首个观测日的那个区间也使用显式 `transition:` 标记的代理收益，因为目标基金还没有前一日的真实收盘价。第二个观测日起完全使用目标基金观测收益。

`IsSyntheticReturn` 描述**该日收益来源**，不是说从此前模拟期累计下来的指数水平变成了真实基金股价。真实账户股数、历史绝对价格阈值、现金分红和拆股不能直接套在重置为 100 的指数上。调整后收益含分配的总回报近似，后续不能再次重复加入同一分红。

### VIX 的特殊边界

VIX 从 Cboe 历史序列读取，保留原始指数水平。下载中若有非美股交易日的指数记录，原文件保留且排除清单写入元数据；请求的美股交易日缺值仍失败，不补值。

Cboe 在 2003 年改变了 VIX 方法，之前的新方法历史属于回溯研究口径，不能直接称为当年实时可得信号。本版保守地把 **2004-01-01 之前的整段（含 2003 年过渡年）** 标为 `HistoricalMethodologyBackcast=True`，而不是声称 2004 年才正式切换。要做严格历史可交易研究，需要另外处理旧 VXO 与新 VIX 的可得时间及阈值差异。

## 输出和读取

```text
data/research/runs/<run_id>/
  inputs/observed/        # 通过 PR #1 校验的原始批次副本
  inputs/supplemental/    # 代理、利率和 Cboe 响应及元数据
  scenarios/base/
  scenarios/higher_financing/
  scenarios/broad_equity_proxy/
  scenarios/combined_stress/
  quality_report.json
  manifest.json
```

清单记录输入来源、下载时间、观察批次 ID、参数、代码哈希、全部文件 SHA-256、截止日期和成功/失败状态。哈希用于检查完整性，不是交易所认证、数字签名或行情绝对正确性的保证。

四个情景用于区分融资敏感性和风格代理敏感性。`quality_report.json` 包含上市后观测区间的模型相关性、年化均值误差、跟踪误差，以及两段危机窗口的**单资产买入持有**回报与回撤。这些不是用户策略的回测结果；重叠期诊断没有被用来拟合早期模型。

```python
from prepare_research import load_research_data

frames = load_research_data(
    end="2026-08-01",
    scenario="base",
    allow_synthetic=True,
    allow_historical_backcast=True,
    execution="close_return_only",
)
```

读取器要求显式承认模拟和 VIX 回溯口径，并检查整个批次哈希、日期范围与状态。VIX 只能作信号。`IndicatorsReady` 为真才有全部预热后的指标。

**没有制造模拟 Open/High/Low/Volume。** 本版只支持收益序列研究，不能据此进行次日开盘成交、盘中止损、限价单或成交量限制模拟。具体执行规则等用户给出策略后单独确定。仅把收盘信号和收益错开一行，也不自动证明可成交：收盘后才知道的信号，不能反过来在同一收盘价下单。

## 本次审查发现的旧流程问题

旧 `get_data.py` 在上市后缺价时仍会借用代理收益填补；模拟阶段把 Open/High/Low 全填成 Close、Volume 填成 0；同一 synthetic 文件没有逐行的来源类型。`yf.download()` 的调整参数未固定，只有存在 `Adj Close` 时又仅替换 Close，会产生潜在口径混用。仓库 SGOV、VIVAX 样本实际只有 OHLCV，没有独立复权因子与公司行为列。

旧 SGOV 算法只在交易日加一次 `/360` 利息，没有按两次交易之间实际经过的天数计息。旧 QLD/TQQQ 固定 annual_decay 不能表示历史融资利率变化。CGDV 经 SCHD、VYM、VIVAX 链式回填掩盖了风格代理的不确定性。

PR #1 保留真实数据且失败阻断的设计仍有价值；但删除所有上市前模拟无法满足这次的危机研究目标。本功能不撤销它，而是在其上建立明确隔离的研究层。

## 资料

- ProShares TQQQ（每日 3 倍目标）：https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq
- ProShares QLD（每日 2 倍目标）：https://www.proshares.com/our-etfs/leveraged-and-inverse/qld
- Schwab SCHD：https://www.schwabassetmanagement.com/products/schd
- Capital Group CGDV：https://www.capitalgroup.com/advisor/investments/exchange-traded-funds/details/cgdv
- FRED DTB3：https://fred.stlouisfed.org/series/DTB3
- FRED DFF：https://fred.stlouisfed.org/series/DFF
- Cboe VIX 方法与历史：https://www.cboe.com/tradable_products/vix/vix_historical_data
- yfinance 参数：https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html
