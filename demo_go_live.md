# BoxChannelScalp 上 demo 操作单

> 生成时间 2026-09-08 · 基于 MT5 Tester 全样本验证(2024-03-04 ~ 2026-08-26)
> 预期:**+32.75% / PF 1.173 / 529 笔 / 胜率 37.8% / 最大回撤 13.08%**

---

## 0. 先读这条(不然会亏钱)

**EA 的 `InpAtrStopMult` 必须是 1.5,不能是源码原来的 1.0。**

- `1.0` → 止损过紧 → **PF 0.79,亏损 −15.17%**(已实测)
- `1.5` → 已验证 → **PF 1.173,+32.75%**

⚠️ 历史上出过两次这个坑:Tester 输入框改了但源码默认没改,挂到图表上用回默认值。
**本次已把源码默认改成 1.5**(L44),但你仍要在挂载时**亲眼确认输入框显示 1.5**。

---

## 1. 挂载前 3 步(缺一不可)

### ① 同步源码到终端
```bash
cp "D:/workbuddy/tushare/XAUUSD/xauusd策略/mql5/BoxChannelScalp.mq5" \
   "C:/Users/18628/AppData/Roaming/MetaQuotes/Terminal/7643C0B96C7AD5841307C9E1EB0B9252/MQL5/Experts/BoxChannelScalp.mq5"
```

### ② F7 编译
- 前置:**EA 未挂在任何图表上、Strategy Tester 未在运行**(否则 `.ex5` 被锁,编译失败)
- MetaEditor 打开终端副本 → F7 → 确认 0 errors / 0 warnings

### ③ 确认终端总开关
- MT5 工具栏 **"自动交易"按钮必须是绿色**(不是只勾 EA 的"允许自动交易")
- 右上角显示 "自动交易 已启用"

---

## 2. 参数表(全部已核对,与验证跑一致)

| 分组 | 参数 | 值 | 备注 |
|---|---|---|---|
| 周期/通道 | InpTrendWindow | 24 | |
| | InpRelSlopeThresh | 0.0004 | |
| | InpShortTrendN | 20 | |
| 聚类 | InpShortWin / InpLongWin | 4 / 4 | |
| | InpMinK / InpClusterBins | 2 / 20 | |
| | InpRevTol | 0.0015 | |
| 入场确认 | InpUseReversalFilter | true | |
| | InpMinWickRatio | 0.6 | |
| | InpRsiPeriod / 超买 / 超卖 | 14 / 70 / 30 | |
| | InpCooldownBars | 2 | |
| 趋势过滤 | InpMAFilter / Period | true / 96 | 核心过滤 |
| **止损/止盈** | InpUseAtrStop | true | |
| | **InpAtrStopMult** | **1.5** | ⚠️ 见第 0 节 |
| | InpRMult | 1.75 | |
| | InpTrailing | true | |
| ATR Regime 门 | InpAtrRegimeGate / Period / Mult | true / 48 / 1.2 | 避高波动 |
| 仓位 | InpFixedLot | 0.01 | = 1 oz,勿改 |
| | InpMaxSpreadPoints | 50 | 实时点差常 ~33,几乎不触发 |
| 隔夜 | InpAvoidOvernight / CloseHour | true / 22 | 服务器时间 |
| | InpStrongOvernight | 0.002 | |
| 保真度 | InpUseServerStops | true | 服务端 SL/TP |
| 其它 | InpMagic | 20260906 | |
| | InpLogTrades / InpHeartbeat | true / true | 必须开,否则无法验证 |
| 时段 | InpTradeStartHour / EndHour | 1 / 22 | DST 自动,= 北京 06:00 起 |

**账户要求**:TMGM demo · XAUUSD · 杠杆 ≥ 1:500 · 图表周期 **M15**

---

## 3. 挂载

1. 打开 XAUUSD **M15** 图表(周期必须是 M15,EA 内部按 M15 取数)
2. 导航器 → Experts → 拖 `BoxChannelScalp` 到图表
3. 弹窗"输入"页 → **逐项核对上表,重点看 `InpAtrStopMult = 1.5`**
4. "常用"页 → 勾选 **允许算法交易**
5. 确定 → 图表右上角应出现 **笑脸图标**(皱眉 = 没开自动交易)

---

## 4. 启动后 5 分钟验证(没看到这些 = 没在跑)

| 检查项 | 位置 | 期望 |
|---|---|---|
| 笑脸 | 图表右上角 | 笑脸,不是皱眉 |
| 初始化日志 | 终端 → Experts 标签 | `BoxChannelScalp 初始化完成 \| 手数=0.01 服务器强平时点=22:00 服务器SL/TP=开` |
| 心跳 | 同上,每 15 分钟一行 | `[HB] ... 新bar=N 时段=IN 实时点差=33` |
| 无报错 | 同上 | 无 `invalid` / `failed` / `error` |

**看不到 `[HB]` 心跳 = EA 没在跑**(这是上一轮踩过的坑,加心跳就是为这个)。
先查:自动交易总开关 → EA 是否允许交易 → 图表周期是否 M15。

---

## 5. 日常监控

**预期频率:约 4 笔/周**(529 笔 ÷ 2.48 年)。这是低频策略,别按高频去期待。

| 频率 | 检查 |
|---|---|
| 每天 | Experts 标签有 `[HB]`(证明存活);账户无异常报错 |
| 每周 | 成交笔数;与预期 ~4 笔/周 对比 |
| 每 2 周 | 把 `MQL5/Files/BoxChannelScalp_trades.csv` 发我分析 |
| 每月 | 算 PF / 胜率 / 回撤,对照第 6 节止损线 |

CSV 路径(实盘/演示盘,注意不是 Tester 那个):
```
C:\Users\18628\AppData\Roaming\MetaQuotes\Terminal\7643C0B96C7AD5841307C9E1EB0B9252\MQL5\Files\BoxChannelScalp_trades.csv
```

---

## 6. 止损线(触发即撤下复盘)

- **PF < 1.0**(成交 ≥ 30 笔后统计)
- **回撤 > 20%**
- **单日亏损 > 5%**
- 连续 2 周 0 成交(非假期)→ 查 EA 是否在跑

---

## 7. 已知风险(上 demo 前必须接受)

### ⚠️ 强 regime 依赖 —— 最重要的风险
分 5 段非重叠区间回测:**S5 主升浪(25-09~26-08)单独贡献 +54.6%,占总收益 92%**;
S1/S3/S4 三段合计仅约 +9%;**S2(24-02~24-10 首波拉升)所有参数下均亏损**(−3.6%~−4.8%,PF 0.80~0.86)。

**→ 本策略只在强趋势段有效。若无趋势行情,预期收益接近 0 甚至为负。**

### ⚠️ 收益数值脆弱(刀刃效应)
平均止损距离只变 −0.6%,回测收益就掉 −19.9pp(放大 33 倍)。
→ **+32.75% 是区间估计,不是承诺**。真实区间 +32%~+43%。

### ⚠️ Python 回测的 +62.5% 不是实盘预期
那是 `atr[0]` 偏乐观取数的产物,已证伪。以 EA 的 +32.75% 为准。

---

## 8. 后续已排期(暂不动)

1. **趋势/regime 前置过滤** —— 解决 S2 亏损 + 非趋势段不赚钱,收益最高
2. **ATR 1.5 → 1.3** —— Python walk-forward 显示 1.3 全面更优(+59.02%/PF1.292/DD9.22%),
   但**尚未在 EA 侧验证**,等 demo 跑出 30+ 笔基线后再切换对比
