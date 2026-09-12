# back_trader_test

下载并校验美股标的的真实观测日线，作为回测输入。**不会生成上市前历史，也不会用代理价格填补缺失行情。**

仓库原有 `data/*_synthetic_daily.csv` 含模拟历史，`data/raw/` 是旧脚本的下载文件，缺乏明确的复权参数和来源元数据。这两类文件仅保留供追溯，均不属于新流程验证通过的数据，回测入口不会读取它们。

## 安装和运行

Python 3.11+，推荐在虚拟环境中安装：

```bash
python -m pip install -r requirements.txt
python get_data.py
```

默认下载 QQQ、SPY、QLD、TQQQ、SGOV、VIX、VOO、VTV、SCHD、CGDV、KO。起点为 1999-03-10；每个标的只保留从供应商声明的历史起点开始的观测值。默认结束日期为纽约当天，**不包含当天**，避免未收盘日线。也可以固定截止日期复现实验：

```bash
python get_data.py --end 2026-08-01
python get_data.py --verify-only --end 2026-08-01
```

此例包括截至 2026-07-31 的数据。可用 `--symbols QQQ SPY TQQQ` 指定子集；验证时使用同一子集。`--start` 包含，`--end` 不包含，所有标的固定使用同一个日期范围。若只有不足 200 行真实历史，MA200 无法预热，整个批次会失败；应提前请求历史，而非填充虚拟数据。

默认输出相对于脚本所在目录，和运行命令时的工作目录无关。`--output PATH` 可指定独立输出目录。

## 数据口径和真实性边界

- 数据来源是 Yahoo Finance，通过 yfinance 获取，并非交易所直接认证的数据。脚本验证的是来源记录、交易日完整性、数值一致性和批次完整性；**不能保证供应商没有错价、修订或历史偏差，也未完成第二数据源交叉核验**。
- 明确使用 `auto_adjust=False, back_adjust=False, actions=True, repair=False, keepna=True`，保存供应商返回的 OHLCV、Adj Close、分红、拆股及元数据。这里的 raw 表示 yfinance 返回表；Yahoo 可能已经做过拆股处理，不应把它理解为未经任何调整的逐笔成交原档。
- 调整版所有 OHLC 同乘 `Adj Close / Close`，成交量保持供应商值。调整价格供总回报近似和技术指标使用，**不是历史实际成交金额**。使用调整价回测时不要再次计入同一分红/拆股；需要真实股数、现金分红、税费等账户模拟时，必须另行明确公司行为和成交口径。
- 不使用 VIVAX/VYM/SCHD 代理补齐历史，不使用利率模拟 SGOV，不使用杠杆公式倒推 QLD/TQQQ。不能用这些较晚上市标的的“真实价格”回测 1999 年。多个标的共同起点在 manifest 的 `common_indicators_start` 中。
- 日期校验使用 exchange-calendars 的 XNYS 日历，包含美股休市日和异常停市；VIX 按同一美股交易日集合做信号校验。`firstTradeDate` 是供应商声明的历史起点，不等于已独立验证的上市日。若供应商或日历存在差异，脚本会报错并要求调查，不自动删行/补行。
- VIX 仅是信号输入，manifest 中标记为不可交易；指数允许零成交量。股票/ETF 的零成交量日会被拦截等待核实，而不是自动认定可成交。
- RSI 使用 Wilder 初始简单平均和递推平滑；恒定价格的 RSI 定义为 50。保留指标预热行，`IndicatorsReady` 为真才可产生依赖全部指标的信号。当天指标只在当天收盘后可用。
- 今日下载的复权历史可能受后来分红/拆股及供应商修订影响，不是当时可获得数据的逐日快照。选股偏差、实际交易成本、成交规则和策略未来信息检查仍需在回测层处理。

## 批次及回测前校验

每次运行建立 `data/verified/runs/<批次ID>/`：

| 文件 | 用途 |
| --- | --- |
| `raw/<标的>.csv` | 供应商返回表，在校验前保存 |
| `raw/<标的>.metadata.json` | 标的、币种、时区、供应商历史起点等元数据 |
| `adjusted/<标的>.csv` | 统一调整后的日线、指标、预热和非合成标记 |
| `manifest.json` | 参数、获取时间、依赖版本、脚本哈希、日期覆盖、文件 SHA-256、校验状态 |

只有全部目标通过检查后，才原子更新 `data/verified/latest.json` 为 `passed`。下载期间状态为 `downloading`，任意下载或校验失败则标记 `failed` 并返回非零退出码。旧批次保留供审计，**最新一次失败后不会自动退回旧批次**。进程被强制终止时状态仍会阻止读取；确认没有下载进程后才可移除遗留的 `.download.lock` 并重跑。

后续回测应调用以下函数，而不要直接 glob CSV：

```python
from get_data import load_verified_data

frames = load_verified_data(["QQQ", "SPY", "TQQQ"], end="2026-08-01")
# end 必须与下载时一致；缺标的、文件变化、最新批次失败均抛出异常。
# frames 保留完整真实历史；策略在 IndicatorsReady=True 后使用指标。
# 多标的需要共同起点时，使用各标的可用指标日期的交集。
```

读取前验证整个批次的清单和文件哈希，显式核对所需截止日期，检查读取期间是否有新批次开始。不提供旧文件导入/自动认证开关，旧数据不能仅凭文件名改成“真实数据”。

由于仓库目前还没有回测引擎，这个入口是供后续回测接入的数据校验接口；直接读取旧 CSV 的外部程序不会自动获得这些保护。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试使用专门构造的离线样例，覆盖复权一致性、上市起点、缺失/重复/错序日期、节假日、缺价、RSI、下载失败、文件损坏、旧批次拦截等。测试数据不是行情，也不会被放入正式数据目录。在线下载受供应商限流影响；离线测试通过不代表在线行情已经获取并核实。

依赖参数说明：[yfinance](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)；交易日历：[exchange-calendars](https://github.com/gerrymanoim/exchange_calendars)。
