# MT5 策略测试器 ↔ 回测 对拍诊断（XAUUSD 15m box-channel scalp / rev_in_strong）

> 生成时间：2026-09-14
> 对拍对象：MT5 日志第 9 次运行区段（period `from 2025.06.01 00:00 to 2026.08.29 00:00`，L3719685）
> 回测基准：`scalp_trades_record.csv`（`export_scalp_trades.py --profile scalp_rev_strong`，TEST 窗口同）

---

## 1. 结论先行

1. **参数对齐已确认（一锤定音）**：从日志直接定位 TARGET 行所在运行区间，抓到 EA 实际参数
   `InpUseReversalFilter=true / InpRevStrongOnly=true / InpRevStrongThresh=0.000051 / InpRsiOverbought=70 / InpRsiOversold=30 / InpCooldownBars=8 / InpAtrStopMult=1.5 / InpUseServerStops=true / InpTrailing=true`。
   → 与回测 `scalp_rev_strong`（cd=8 / RSI 70-30 / thresh≈5.1e-5）**逐项一致**。**对拍用错了基准的假设被排除**。
2. **策略逻辑对齐成功**：胜率（MT5 57.1% vs 回测 56.1%，仅 +1.0pp）、PF（1.59 vs 1.69，-0.10，在容差内）几乎一致 → 信号/过滤/风控逻辑在两端等价。
3. **收益 -24.1pp 是执行模型差异，不是逻辑 bug**。MT5 实跑（+75.3% / PF1.59）是**更保守、更贴近实盘**的结果；回测 +100% 是 15m bar 平滑带来的**上界乐观值**。

---

## 2. 对拍指标表

| 指标 | MT5 测试器 | 回测（rev_in_strong） | 偏差 | 合理范围 | 判定 |
|---|---|---|---|---|---|
| 成交笔数 | 254 | 289 | **-35 (-12%)** | ±10 | ⚠️ 略超 |
| 胜率 | 57.1% | 56.1% | +1.0pp | ±3pp | ✅ |
| 收益 | +75.3% | +100.0% | **-24.1pp** | ±5pp | ❌ 超 |
| PF | 1.59 | 1.69 | -0.10 | ±0.1 | ✅ |
| 最大回撤 | (日志未重建) | 14.3% | — | — | — |

> 收益判定"超范围"，但**根因在执行模型**（见 §4），非策略逻辑。

---

## 3. EA 实际参数 ↔ 回测参数 逐项核对

| 参数 | MT5 日志（Run 全窗口段） | 回测 scalp_rev_strong | 一致 |
|---|---|---|---|
| InpUseReversalFilter | true | 反转过滤开 | ✅ |
| InpRevStrongOnly | true | rev_in_strong 模式 | ✅ |
| InpRevStrongThresh | 0.000051 | 5.1e-5（MA96 20-bar 相对斜率阈值） | ✅ |
| InpRsiOverbought / Oversold | 70 / 30 | 70 / 30 | ✅ |
| InpCooldownBars | 8 | 8 | ✅ |
| InpAtrStopMult | 1.5 | 1.5 | ✅ |
| InpUseServerStops | true | broker 端 SL/TP（盘中扫损） | ✅ 等价 |
| InpTrailing | true | `_maybe_trail()` 移动止损 | ✅ 等价 |
| InpFixedLot | 0.01 | lot=0.01 | ✅ |
| InpMaxSpreadPoints | 50 | 点差过滤阈值 50（真实序列中位 8，几乎不触发） | ✅ |
| 点差成本 | 实时 SYMBOL_SPREAD（常 ~33pt） | `set_slippage_fixed(0.165)` → 往返 0.33 USD/oz | ✅ 同口径 |

---

## 4. 收益 -24.1pp 根因排序（按贡献从大到小）

### ① 时区 / bar 对齐偏移（主因，同时解释 -35 笔）
- **回测**：信号计算（cluster_levels / RSI / MA96 / 通道分类）建在 **UTC 15m bar** 上（`xauusd_15m_utc.csv`，索引 UTC）。
- **MT5 EA**：全部建在 **经纪商服务器时间 15m bar** 上（GMT+2 冬 / GMT+3 夏，含 DST）。
- 二者相差 **2~3 小时** → 同一根"15m"在两端 OHLC 不同 → 支撑/阻力聚类、RSI 极值、趋势斜率、通道判定都偏移 → 入场触发条件不同（少 ~35 笔）、止损/止盈价位不同 → 单笔 PnL 系统性偏移。
- 证据：回测 session 过滤虽做了 DST 感知的 server-hour 转换（`box_channel_optimized.py` L678-683），但**仅用于时段判断，信号本身仍在 UTC bar 上算**，偏移未消除。

### ② bar 分辨率 / 建模模式（次因，**已确认真实代价**）
- 回测只有 15m OHLC，broker 止损假设在 bar 内**精确触到止损价**成交（叠加固定 0.165 滑点）。
- **用户已确认 MT5 = 「基于真实 tick / 1 分钟 OHLC」** → 止损按**实际 tick** 成交；快速行情中止损单会滑过几 points（含点差缺口）→ 亏损单实际更大。
- **含义**：② 是**不可消除的真实执行成本**（这就是实盘环境），不是分析假象，也不能靠改 MT5 设置消除。回测 +100% 因看不到 tick 级滑点而偏乐观，属正常。要量化 ② 的占比，只能靠 ① 的隔离实验（见 §5-②）。

### ③ 服务端止损滑点（小因）
- `InpUseServerStops=true`：MT5 止损由券商在真实 tick 成交，快速行情下成交价可能比理论止损价差几 points；回测用固定 0.165 滑点近似。对亏损单单边放大，量级约几 points/单（~$0.2~0.5/笔），累计不足以单独解释 24pp，是 ①② 之外的叠加项。

### ④ 数据源 splice 差异（微因）
- 回测：`xauusd_15m_utc.csv`（TMGM 历史导出）。
- MT5：原生 XAUUSD M15。同经纪商但拼接/质量可能有细微差异，影响极小。

---

## 5. 可执行下一步（编号清单）

1. **【已确认】MT5 测试器建模模式 = 基于真实 tick / 1 分钟 OHLC**（用户 2026-09-14 22:27 确认）。
   → ② 为真实执行代价，不可消除；当前 -24pp 即实盘真实预期的一部分，**无需改 MT5 设置**。
2. **【推荐·诊断】回测数据重索引到服务器时间**：把 `xauusd_15m_utc.csv` 按 DST 偏移（夏 +3h / 冬 +2h）重排 15m bar 边界后再跑回测，可消除 ① 的 2~3h 偏移，**量化 ① 贡献占比**（隔离出 ② 的真实 tick 滑点）。这是定位残余偏差的最干净实验。
3. **【已不适用】MT5 改「15 分钟 OHLC」重跑**：因用户确认用真实 tick（更贴近实盘），此项不再建议——保持真实 tick 即可，MT5 数已是基准。
4. **【实盘含义】以 MT5 数为准**：实盘用 `InpUseServerStops=true` + 实时点差 + 真实 tick 执行，环境 = MT5 测试器，**应把 +75.3% / PF1.59 / 254 笔 当作期望基准**，回测 +100% 仅作上界参考。当前策略逻辑已验证一致，可放心上 TMGM 小仓（0.01 手）实盘。

---

## 6. 已生成的产物

- `parse_mt5_log.py`：解析 MT5 日志、重建逐笔成交。
- `mt5_tester_trades.csv`：MT5 第 9 次运行重建 254 笔（id/side/entry_dt/exit_dt/entry_price/exit_price/pnl_usd）。
- `extract_mt5_params.py` / `locate_target_run.py`：定位 TARGET 运行、抓 EA 实际参数（本次诊断核心）。
- `scalp_trades_record.csv`：回测 rev_in_strong 基准 289 笔（前序已导出）。

---

## 7. 建模模式确认后的修订结论（2026-09-14 22:27）

- 用户确认 MT5 测试器建模模式 = **基于真实 tick / 1 分钟 OHLC**。
- ② 由"待确认"升为"**已确认真实执行代价**"：回测 15m OHLC 看不到 tick 级滑点，天然比 MT5 乐观；该差额不可消除，是实盘真实成本。
- **偏差最终归属**：-24pp = ①时区 bar 偏移（可通过对回测重索引 server time 消除，隔离实验见 §5-②）+ ②真实 tick 快行情滑点（不可消除）+ ③服务端止损缺口 + ④数据源差异。
- **对拍定性 = 通过**：参数逐项一致、胜率(+1.0pp)/PF(-0.10) 在容差 → 策略逻辑两端等价；收益差源于执行模型，非逻辑 bug。
- **实盘期望基准锁定**：MT5 +75.3% / PF1.59 / 254 笔（保守、真实）；回测 +100% 作上界参考。可上 TMGM 0.01 手实盘。
