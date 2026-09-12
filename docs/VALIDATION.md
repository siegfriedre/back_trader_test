# 已完成的运行验证（2026-09-12）

## 本次验证的代码与运行

- 被测代码提交：`fdbf2fe4be12f3c8eeef184015d04cdaceb38813`。
- [GitHub Actions 实际运行](https://github.com/siegfriedre/back_trader_test/actions/runs/34700241483)：所有步骤成功，包括完整回归测试、仓库 CSV 对比和报告上传。
- 完整测试命令：`python -m unittest discover -s tests -v`，**96 tests / OK**。包括原有24项观测数据测试、31项研究模型测试和本次41项回测测试。测试样例是人为构造的数据，不是历史收益证据。
- 实际仓库数据命令：`python run_comparison.py --accept-legacy-adjusted`，**75 个组合全部完成**，不是仅在人造样例上跑通。
- 结果批次：`20260912T144631Z-3c84af42`，manifest 状态 `passed`。
- [运行结果 artifact](https://github.com/siegfriedre/back_trader_test/actions/runs/34700241483/artifacts/10300152098)，包含385个文件（完整报告、逐笔交易、事件、每日仓位、参数与哈希）。Actions 保留期14天，应另行保存需要长期保留的结果。
- 归档 ZIP SHA-256：`11ae5bf05511ea93e3002d7e672e91ff82932b36c8f09402e69b416c7c167726`。下载后已核对该归档哈希，解压后又用 `--verify-run` 核对整个输出清单和各文件哈希，通过。

## 实际覆盖范围

| window | 账户记录起点 | 第一笔策略配置 | 截止日（包含） | 组合数 |
|---|---|---|---|---:|
| post_tqqq | 2010-02-11 | 2010-02-11 | 2026-07-31 | 25 |
| long_history | 1999-03-10 | 1999-12-22 | 2026-07-31 | 25 |
| all_observed | 2020-06-01 | 2020-06-01 | 2026-07-31 | 25 |

long_history 在完整指标预热前持有无息USD，期间按既定规则定投；没有为1999年3月凭空生成MA200。所有对照使用相同预热约束。all_observed 的25个组合中，`synthetic_held_return_days` 均为0。

## 不能由本次验证推出的结论

成功执行不等于策略在未来有效，也不等于旧价格已独立认证。本次使用的是仓库旧 `data/raw` 文件，在明确接受 Close 按调整价使用的假设后运行；旧文件没有完整公司行为/复权/下载元数据，数据来源风险仍然存在。

post_tqqq 使用上市后的股票观测收益，但SGOV成立前仍是现金代理；long_history 对晚成立基金及早期VTV信号使用明确标记的模型。合成部分不是基金真正的上市前历史。未重新下载全部行情，也没有完成第二供应商交叉核验。

交易为至少滞后一天的收盘收益指数研究，按分数金额记账，默认单边成本5bps，不含税、真实券商成交及结算限制。模型费用和融资是固定假设，没有依据本次结果重新调参。

每种策略定义见 [策略索引](strategies/README.md)，启动与Review方式见 [运行指南](../BACKTEST_GUIDE.md)。本验证记录只增加说明，不改变被测回测代码，不修改原始CSV、不合并main、不新建PR。
