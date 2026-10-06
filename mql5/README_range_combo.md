# BoxChannelRangeCombo EA

把 `xauusd_range_boundary_reversion.py` 中最终采用的 `COMBINED_SELECTED` 策略移植为独立 MT5 EA。

- 源码：`mql5/BoxChannelRangeCombo.mq5`
- 编译产物：`mql5/BoxChannelRangeCombo.ex5`
- 编译结果：`0 errors, 0 warnings`
- 默认 magic：`20260921`
- 默认日志：`MQL5/Files/BoxChannelRangeCombo_trades.csv`

## 策略规则

### 区间状态

```text
1h 24 根收盘线性回归相对斜率绝对值 < 0.0004
15m 24 根效率比 ER < 0.35
15m 前 24 根区间宽度 / ATR 在 2.0 至 6.0
区间高低点不包含当前信号 bar
```

### 多单

```text
低点跌破下边界 0.20 ATR
同一根收盘回到下边界上方 0.10 ATR
RSI(14) <= 35
止损 = 假突破低点下方 0.50 ATR
目标 = 区间中轨
最小盈亏比 = 0.80
```

### 空单

```text
1h 收盘低于 24 根 1h bar 之前的收盘
高点突破上边界 0.10 ATR
同一根收盘回到上边界下方 0.05 ATR
RSI(14) >= 60
止损 = 假突破高点上方 0.50 ATR
目标 = 入场价到区间中轨的 50% 位置
最小盈亏比 = 0.50
```

### 共享控制

```text
同 bar 多空同时成立：跳过
同方向冷却：4 根
时间止损：12 根
默认规避隔夜
最多 1 个持仓
固定 0.01 手
```

## 推荐 Strategy Tester 配置

| 项目 | 值 |
|---|---|
| 品种 | XAUUSD |
| 周期 | M15 |
| 初始资金 | $1,000 |
| 杠杆 | 1:1000 |
| 手数 | 0.01 |
| 执行 | 真实 tick 或 1 分钟 OHLC |
| ServerStops | true |
| 交易时段 | 服务器 1:00-22:00 |
| 点差 | broker real spread |

建议窗口：

| 窗口 | 起止 |
|---|---|
| IS | 2023-09-13 至 2025-06-30 |
| OOS | 2025-07-01 至 2026-09-08 |
| FULL | 2023-09-13 至 2026-09-08 |

Python 基准：

| 窗口 | 收益 | PF | 最大回撤 | 笔数 |
|---|---:|---:|---:|---:|
| IS | +7.87% | 1.38 | 2.38% | 141 |
| OOS | +29.59% | 2.34 | 3.82% | 77 |
| FULL | +37.48% | 1.86 | 3.61% | 222 |

MT5 实跑预期通常会低于 Python 上界，主要差异来自：

1. MQL5 信号基于 broker 服务器时间 bar，Python 使用 UTC 数据。
2. ATR 数据源和 shift 对齐可能存在少量差异。
3. 市价单实际成交价与信号收盘价不同。
4. 快速行情 Stop/Limit 成交包含真实 tick 滑点。
5. broker swap、点差和交易时段可能与 Python 模型不同。

对拍重点应放在：

- 月度成交时间是否一致
- 多空方向和假突破结构是否一致
- 笔数数量级是否一致
- 最差月份和回撤是否同向
- 不要求收益率逐笔完全一致

## 部署步骤

1. 把 `BoxChannelRangeCombo.mq5` 复制到：

```text
<MT5 Terminal Data Folder>\MQL5\Experts\
```

2. 在 MetaEditor 中打开并执行 `F7`。
3. 确认 `0 errors, 0 warnings`。
4. 挂到 XAUUSD M15 图表。
5. 确认 `InpMagic` 与趋势 EA 不同。
6. 打开成交 CSV 日志。
7. 若与趋势 EA 并联运行，确保两个 EA 的 magic number、日志文件和风控计数相互独立。

## 已知限制

- 目前未做 MT5 tester 与 Python 的逐笔正式对拍。
- 空头策略样本较少，F5 阶段贡献较高。
- EA 没有实现趋势 box 逻辑，只实现区间假突破组合。
- 当前尚未验证两个 EA 在 demo 上同时运行时可能出现的保证金变化。
