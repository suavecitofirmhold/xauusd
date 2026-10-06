# XAUUSD Strategy

TMGM XAUUSD 策略研究、回测与优化工作区。

## 仓库范围

仓库只保留可复现研究和实盘落地所需的核心资产：

- 策略与回测/实验源码；
- MT5 策略源码与导出工具；
- 服务器时间原始行情数据；
- 最终结论报告和小型汇总结果表。

大型逐笔明细、信号文件、权益曲线、图表、编译产物、Python 缓存和备份文件不入库。

## 主要目录

- `优化/`：SR+ML 执行重构、执行质量层、组合状态机前瞻验证与结论报告。
- `mql5/`：MT5 策略源码与导出工具。
- `server_data/`：服务器时间行情导出数据。

## 核心入口

- `box_channel_optimized.py`：Box Channel 主回测引擎。
- `xauusd_box_channel_range.py`：Box Channel Range Combo 策略。
- `rl_sizing_experiments.py`：RL 仓位扩展与回撤节流实验。
- `优化/portfolio_state_lab.py`：组合状态机、并发上限、区域去重与风险热验证。
- `优化/execution_quality_lab.py`：执行质量与成交路径模型。
- `优化/sr_execution_lab.py`：SR+ML 执行几何重构实验。

## 结果入口

核心结论见 `优化/restructure_findings.md`。
