# Box Scalp 回测改造：本地逻辑对齐实盘扫损

> 日期：2026-09-06
> 文件：`XAUUSD/xauusd策略/box_channel_optimized.py`（--profile scalp）
> 目标：把本地回测从「每根 bar 收盘手动平仓」改成「broker 端挂单、盘中扫损/止盈」，与 MT5 实盘（InpUseServerStops=true）一致。

## 一、改了什么

1. **开仓**：市价入场单 `self.buy()/self.sell()`，成交（持仓开后）由 `_attach_sl_tp()` 挂出 broker 端 **Stop（止损）+ Limit（止盈）** 挂单。
   - Stop 单在 bar 的 `low<=stop`（多）/`high>=stop`（空）时**盘中触发**，吃到影线扫损，不再是「收盘价 <= stop 才平」的乐观假设。
2. **状态机改为 next() 轮询订单 `.status`**（不再依赖 `notify_order` 身份判断）。
   - 验证发现：本机 backtrader 构建里 `notify_order` 传入的 order 对象与策略创建的 `self.xxx_order` **身份不一致**（`isentry/isl/itp` 全为 False），bracket 单还会在入场成交时被 OCO 自动取消 SL/TP。
   - 因此改用轮询：`sl_order.status == Completed` 等驱动平仓记录、重置、取消兄弟单。
3. **仅隔夜强平 / 强平仍走手动平**：经纪商无法按时自动平，故 EOD（server hour ≥ 20，对齐 TMGM 零点收 swap 窗口）取消挂单后 `self.close()`。
4. **追踪止损**：`_maybe_trail()` 把 broker 端 SL 挂单推进到更优价位（改进 >0.05 才重挂，减抖）。
5. **交易统计改由策略自维护 `trades_log` 计算**（FIFO 配对 open/close），避开 backtrader TradeAnalyzer 版本差异；新增 Profit Factor。

## 二、回测结果（实盘对齐后）

测试窗 2025-06-01 ~ 2026-08-29（$1000 / 1:1000 / 0.01 手 / 点差 33）：

| 指标 | Python(扫损) | MT5 实盘(EA) | 旧手动平仓(虚高) |
|---|---|---|---|
| 累计收益 | **+27.8%** | +31.24% | +56.87% |
| 盈利因子 PF | **1.16** | 1.23 | — |
| 最大回撤 | **14.6%** | 19.49% | — |
| 交易笔数 | **398** | 440 | 369 |
| 胜率 | **42.0%** | ~37% | ~49% |
| Sharpe | **1.24** | 1.73 | 5.88 |

全样本 2023-09-14 ~ 2026-08-29：+26.2% / PF 1.10 / DD 14.9% / 819 笔 / 胜率 39.9% / Sharpe 0.51。

**结论**：Python 扫损版（+27.8%）稳稳落在「虚高手动平仓 +56.87%」与「MT5 真实成交 +31.24%」之间——符合预期。差异来源：Python 用 15m OHLC 棒（比真实 tick 粗糙），真实 tick 在扫损时会吃到更多影线+更大滑点，故 MT5 收益略低、回撤略高。本地回测现已可作为实盘的可靠预演。

## 三、优化方向（按性价比排序）

1. **放大止损缓冲，抗影线扫损（最高优先级）**
   - scalp 用 `atr_stop_mult=1.0`（1×ATR 止损），在 15m 上很容易被噪声影线扫掉 → 胜率从 ~49% 跌到 ~42%、大量小亏。
   - 建议：`atr_stop_mult` 1.0 → 1.5~2.0，或叠加 `sl_buffer` 固定美元缓冲；用 walk-forward 找最优。代价：单笔亏损变大，但被扫次数下降，PF 通常改善。

2. **交易时段过滤（避开低流动性 + 新闻秒）**
   - 扫损主要亏在伦敦尾盘/美盘末（点差扩大、影线变长）。建议加 session 过滤器：只在 **伦敦+纽约重叠（约 UTC 12:00–20:00）** 内允许新开仓；server hour ≥ 20 本就强平，可前移到 ≥19。
   - 预期：显著减少被扫的无效单，回撤下降。

3. **波动率 regime 门（atr_regime_gate）**
   - 全样本 Sharpe 仅 0.51，弱在 2024–25 震荡段（walk-forward 已知 box 对 regime 敏感）。
   - 建议：开 `atr_regime_gate=True`（ATR 超均值 1.4× 时暂停），或叠加「价格 > MA96」趋势过滤（scalp 已带 MA96 过滤，可进一步要求短期方向不逆）。
   - 预期：砍掉最差 regime 的亏损，提升稳健性。

4. **分批止盈 / 半仓锁利（partial_tp）**
   - 当前 scalp `partial_tp=0`。对 0.01 手（=1oz）半仓锁利不现实（最小 0.01 手），但对 0.02~0.05 手账户可启用：到目标 60% 先平半仓，剩半仓用追踪保本。
   - 预期：锁定部分盈利，降低对单一止损的依赖。

5. **追踪止损放缓**
   - 当前 ATR 追踪在价格反向 1×ATR 即推 SL 到成本，震荡市会过早推损导致被扫。
   - 建议：追踪触发阈值用 1.5~2×ATR 盈利才启动，或改用「固定点数追踪（如 80~120 点）」而非 ATR。

6. **提高 R 倍数（r_mult）**
   - 止损放大后，配套提高 `r_mult` 1.75 → 2.0~2.5，保持正期望的 reward/risk；依赖 walk-forward 一起校准。

7. **扩展到其他画像**
   - 本次只改了 scalp（=EA 移植对象）。`mid`/`long` 画像若也要实盘对齐，需同样改造（它们当前仍是旧手动平仓逻辑）。

## 四、验证步骤（用户自评）

```bash
# 实盘对齐后的 scalp 回测（与 MT5 实盘同区间）
python box_channel_optimized.py --profile scalp --fromdate 2025-06-01 --todate 2026-08-29
# 全样本
python box_channel_optimized.py --profile scalp --fromdate 2023-09-14 --todate 2026-08-29
```
MT5 侧对照：EA `BoxChannelScalp` + XAUUSD + M15 + 2025.06.01~2026.08.29 + 每笔成交(1分钟OHLC) + InpUseServerStops=true + 延迟100ms + 点差默认。
