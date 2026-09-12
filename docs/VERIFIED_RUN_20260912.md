# 已核实的完整运行记录：2026-09-12

此记录来自GitHub插件读取的实际workflow状态、job日志和artifact上传记录，不是本地人造样例的回测收益。

- 实现提交：`fdbf2fe4be12f3c8eeef184015d04cdaceb38813`
- 分支：`feat/research-preinception-data`
- 工作流：`Offline strategy comparison`
- Run ID：`34700241483`
- Job ID：`103570595999`
- 状态：completed / success
- 原仓库完整测试：`python -m unittest discover -s tests -v`，96项通过。
- 实际回测命令：`python run_comparison.py --accept-legacy-adjusted`
- 日志明确记录：`Completed 75 cases`。
- 结果批次：`20260912T144631Z-3c84af42`
- Artifact名称：`strategy-comparison-fdbf2fe4be12f3c8eeef184015d04cdaceb38813`
- Artifact ID：`10300152098`；大小41,277,162字节；上传记录385个文件。
- Artifact SHA-256：`11ae5bf05511ea93e3002d7e672e91ff82932b36c8f09402e69b416c7c167726`。

运行页：https://github.com/siegfriedre/back_trader_test/actions/runs/34700241483

产物页：https://github.com/siegfriedre/back_trader_test/actions/runs/34700241483/artifacts/10300152098

下载产物后打开其中的report.html/report.md，或检查summary.csv和各case的交易日志。Artifact有保留期，应及时保存；过期后可以在同样输入与参数下本地重新运行。

## 边界与版本一致性

96项指上述实现提交的GitHub完整测试，不等于开发期间另一套本地草案的测试计数。最终保留已在仓库跑通的 `backtest/` 模块和 `run_comparison.py`，没有将并行准备的重复引擎覆盖到分支。

后续新增的 `review_events.py` 只读取日志，8项人造日志测试已在本地通过；不改变策略引擎、参数、原始CSV或上述75组结果。后续提交的CI通过情况应查看其对应run，而不是把这个run的状态冒充成所有未来提交的状态。

这是基于旧CSV调整口径假设的研究回测。数据来源可审计、程序运行成功，不等于行情经过独立认证，也不证明任何策略未来有效。TQQQ上市前为模拟，post_tqqq早期SGOV仍有代理，long_history共同指标预热期间持有无息USD。默认交易成本为单边5bp，实际交易与税费等仍可能导致偏差。

未重开PR、未合并main、未改写原始行情文件。
