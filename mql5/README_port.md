# BoxChannelScalp — TMGM MT5 (MQL5) 移植说明

把 backtrader 回测 `box_channel_optimized.py` 的 **scalp（趋势过滤 MA96）** 画像，忠实移植为 TMGM MT5 原生 EA。

- **编译状态**：✅ `0 errors, 0 warnings`（MetaEditor X64 Regular，593ms）
- **产物**：`MQL5\Experts\BoxChannelScalp.ex5`
- **源码**：本项目 `mql5\BoxChannelScalp.mq5`（修改源码后需重新复制 + 编译）
- ⚠️ **2026-09-14 源码已更新**：反转过滤由「常开」改为「仅强趋势段开启」，新增 `InpRevStrongOnly` / `InpRevStrongThresh` 两个输入（默认即条件化模式）。旧 `.ex5` 已过期，须按下面步骤二重新复制 + 编译。

---

## 一、本机路径

| 用途 | 路径 |
|---|---|
| 源码（版本管理） | `D:\workbuddy\tushare\XAUUSD\xauusd策略\mql5\BoxChannelScalp.mq5` |
| MT5 源码（编译用副本） | `C:\Users\18628\AppData\Roaming\MetaQuotes\Terminal\7643C0B96C7AD5841307C9E1EB0B9252\MQL5\Experts\BoxChannelScalp.mq5` |
| 编译产物 | 同上目录 `BoxChannelScalp.ex5` |
| 编译器 | `C:\Program Files\TMGM MT5 Terminal\MetaEditor64.exe` |
| 成交日志（运行后生成） | `MQL5\Files\BoxChannelScalp_trades.csv` |

## 二、部署步骤

1. **改源码**（若需要）：编辑项目里的 `mql5\BoxChannelScalp.mq5`
2. **复制到 MT5 并编译**：
   ```bash
   TD=/c/Users/18628/AppData/Roaming/MetaQuotes/Terminal/7643C0B96C7AD5841307C9E1EB0B9252
   cp "/d/workbuddy/tushare/XAUUSD/xauusd策略/mql5/BoxChannelScalp.mq5" "$TD/MQL5/Experts/"
   "/c/Program Files/TMGM MT5 Terminal/MetaEditor64.exe" /compile:"C:\Users\18628\AppData\Roaming\MetaQuotes\Terminal\7643C0B96C7AD5841307C9E1EB0B9252\MQL5\Experts\BoxChannelScalp.mq5" /log
   ```
   看结果：`iconv -f UTF-16LE -t UTF-8 "$TD/MQL5/Experts/BoxChannelScalp.log" | grep -E "error|Result"`
3. **挂到图表**：MT5 → 导航器 → EA 交易 → `BoxChannelScalp` → 拖到 **XAUUSD M15** 图表
4. **勾选**：`工具→选项→EA交易` 里勾「允许自动交易」；图表上 EA 按钮变蓝
5. **先跑模拟账户**（已在参数里按你的要求默认配置）

## 三、参数对照表（EA ↔ 回测）

| EA 输入参数 | 默认 | 回测对应项 | 说明 |
|---|---|---|---|
| `InpTrendWindow` | 24 | `trend_window` | 1h 通道窗口 |
| `InpRelSlopeThresh` | 0.0004 | `rel_slope_thresh` | 通道斜率阈值 |
| `InpShortTrendN` | 20 | `short_trend_n` | 15m 短周期方向 |
| `InpShortWin` / `InpLongWin` | 4 / 4 | `short_win` / `long_win` | 聚类窗口 |
| `InpMinK` | 2 | `min_k` | 密集区最少K线 |
| `InpClusterBins` | 20 | `cluster_bins` | 直方图分箱 |
| `InpRevTol` | 0.0015 | `rev_tol` | 反转容差 |
| `InpUseReversalFilter` | true | `use_reversal_filter` | 反转K线过滤总开关(false=完全关闭) |
| `InpRevStrongOnly` | true | `rev_mode="rev_in_strong"` | 条件化开关: true=仅强趋势段开启(MA96相对斜率>=阈值); false=全程开启(原行为) |
| `InpRevStrongThresh` | 5.1e-5 | `rev_strong_thresh` | 强趋势判定阈值(\|MA96 20-bar相对斜率\|), 训练段2023-2025校准(Round5/6留出验证) |
| `InpMinWickRatio` | 0.6 | `min_wick_ratio` | 影线比例 |
| `InpRsiPeriod/Overbought/Oversold` | 14/70/30 | 同名 | RSI 极端过滤 |
| `InpCooldownBars` | 2 | `cooldown_bars` | 同向冷却 |
| **`InpMAFilter`** | **true** | `ma_filter` | **趋势过滤（已采用）** |
| **`InpMAFilterPeriod`** | **96** | `ma_filter_period` | **EMA(96) 方向门** |
| `InpUseAtrStop` | true | `atr_stop` | ATR 止损 |
| `InpAtrPeriod` / `InpAtrStopMult` | 14 / 1.5 | 同名 | 止损=1.5×ATR |
| **`InpRMult`** | **1.75** | `r_mult` | **止盈=1.75×风险** |
| `InpTrailing` | true | `trailing` | 追踪止盈 |
| **`InpFixedLot`** | **0.01** | `lot` | **验证推荐手数** |
| `InpMaxSpreadPoints` | 50 | *（回测无）* | 点差上限，超过跳过 |
| `InpAvoidOvernight` | true | `avoid_overnight` | 隔夜强平 |
| `InpServerCloseHour` | 22 | *（回测 UTC 20）* | 服务器时间 22:00 强平 |
| `InpStrongOvernight` | 0.0020 | `strong_overnight_thresh` | 强趋势放行 |
| `InpUseServerStops` | true | *（回测无）* | 见下方「偏差 ①」 |

## 四、⚠️ 与回测的三处偏差（必须知道）

### ① 止盈触发方式不同（影响最大）
- **回测**：在 **bar 收盘** 判定 `close <= stop`（backtrader 的 `next()` 按 bar 推进）
- **实盘默认**：挂**服务器 SL/TP**，**盘内**触发
- **后果**：实盘可能被盘内毛刺扫损，而回测不会 → **实盘止损笔数可能多于回测**
- **对拍开关**：把 `InpUseServerStops = false` → EA 不挂服务器 SL/TP，改为**收盘时手动判定平仓**，逻辑更贴近回测（但失去跳空保护，仅建议用于对拍验证）

### ② 点差
- 回测固定 0.33 美元/盎司（单边滑点 0.165）
- 实盘浮动，数据/开盘时段会暴涨 → EA 加了 `InpMaxSpreadPoints=50`（约 $0.50）过滤，超阈值不入场

### ③ 时间与隔夜费
- 回测用 UTC 时间，并**手工建模** TMGM 隔夜费（swap_long=-72.5点 / swap_short=+30.72点 / 周三3倍）
- EA **不需要建模 swap** —— MT5 由经纪商自动收取
- EA 改用 **MT5 服务器时间** 判断强平时点（`InpServerCloseHour=22`，即服务器零点前 2 小时），**绕开 GMT+2/+3 的 DST 换算**，与回测 UTC20 等效

## 五、验证步骤（按顺序）

1. **策略测试器回测**（先做）
   - MT5 → 查看 → 策略测试 → EA=`BoxChannelScalp`，品种 XAUUSD，周期 M15
   - 区间建议与回测对齐：`2025-06-01 ~ 2026-08-29`
   - 模式：`1 分钟 OHLC`（快）或 `每笔订单`（慢但准）
   - 对比回测基准（EA 当前 = rev_in_strong + cd=8 模式，TEST 窗口 2025-06-01~2026-08-29）：**收益 ≈ +100.0% / 289 笔 / 胜率 ≈ 56.1% / PF ≈ 1.69 / 最大回撤 ≈ 14.3%**（$1000 本金、0.01 手；旧值「377 笔 / 48.0% / PF 1.26 / +52.0%」为 rev_in_strong 落地前的旧配置，已作废）
   - 允许偏差，重点看**方向一致**（收益为正、PF 接近、笔数同量级）
2. **逐笔对拍**
   - 导出测试器成交报告，与本目录 `scalp_trades_record.csv` 比对入场时间/方向/价格（该 CSV 由 `python export_scalp_trades.py --profile scalp_rev_strong` 在 TEST 窗口导出，即回测端 rev_in_strong+cd=8 的逐笔记录，与 EA 当前模式对应；EA 常开基线对照另可用 `--profile scalp`）
3. **模拟账户前向运行**
   - 挂在模拟账户 XAUUSD M15，先观察 **1~2 周**
   - 确认：无重复下单、无重仓异常、EOD 22:00 确实平仓、成交日志正常写入

## 六、$150 账户的风险提示（来自本次验证）

| 手数 | TEST 收益/回撤 | 最低权益 | 单笔风险(均) | 末期风险 |
|---|---|---|---|---|
| **0.01**（EA 默认） | +346.6% / 56.3% | $105（−30%） | $9.29 = **6.2%** | $10.07 = **6.7%** |
| 0.02 | +693.3% / 74.6% | $60（−60%） | $18.58 = **12.4%** | $20.15 = **13.4%** |

- **$150 + 0.01 手单笔风险 6.7%，是审慎上限（1~2%）的 3.4~7 倍**，属资金不足/激进
- 因最小手数即为 0.01，**降风险的唯一办法是提高本金**：压到 1% 需约 **$1,007**，压到 2% 需约 **$504**
- 4 组回测 **0 次强平**，但那是"策略全程盈利"的结果；样本外连亏 8~12 笔即接近归零
- 若账户支持 0.001 微型手，请改 `InpFixedLot=0.01→0.001` 并相应放大其余风控

## 七、已知限制

- EA 设计为**单持仓**（与回测一致）：持仓时不新开仓
- 仅实现 `box` 入场风格（回测还有 ma_cross/boll/donchian，未移植）
- 已实现 `atr_regime_gate`（`InpAtrRegimeGate=true` 默认开启，对应回测 `atr_regime_gate` on 1.2；原"未实现"记录已作废，见 §三 参数表）
- 未实现 `partial_tp` 半仓锁利（scalp 画像未启用，默认 0）
- 未实现自适应 r_mult（验证显示劣于固定 1.75，未采用）
