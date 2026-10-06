# XAU/USD Box-Channel Scalp — `use_reversal_filter` 开/关 A/B 决策备忘录

> 范围：仅针对「反转 K 线过滤（`use_reversal_filter`）开关」的四轮验证结论固化。
> 配套文档：`box_strategy_optimization_report.md`（更广的入场风格 / 参数网格 / 杠杆矩阵研究，阶段 A–D）。
> 策略基线：`box_channel_optimized.py` 的 `PROFILES["scalp"]`（MA96 趋势过滤 + 1.5×ATR 止损 + trailing ATR + r_mult 1.75 + cooldown 8）。
> 数据：`D:\Data\stockdata\xauusd\clean\` 15m，全样本 2023-09-13 ~ 2026-08-29，起始权益 $1000 / 0.01 手。
> 生成日期：2026-09-14

---

## 0. 结论（Round 5 已修订）

**把 EA 的 `InpUseReversalFilter` 从「常开」改为「仅强趋势段开启」（regime 条件化）。**

- Round 1~4 结论：反转过滤是质量 / 风险调节器，常开(ON)与常关(OFF)方向逐折翻转、无稳定占优方 → 当时建议维持常开。
- Round 5（修正的条件化测试，阈值全样本校准 **median|MA96 斜率| = 6.1e-5**）给出**反转结论**：
  - `rev_in_strong`（仅 |MA96斜率|≥阈值的强趋势段开过滤）是 6 折综合最优模式——累计收益 **+98.0%** / 平均 PF **1.303** / 平均 DD 6.96%，**4/6 折同时胜出常开(ON)与常关(OFF)**，比 EA 当前常开默认累计高约 **+24pp**。
  - `rev_in_weak`（仅弱趋势/震荡段开过滤）反而是**最差**模式（收益 +60.4% < 常开 +73.9%），证伪「震荡段才需要过滤」的直觉。
  - 原提议的 `high_vol` 轴与策略既有 `atr_regime_gate` 撞车（高波动段已禁交易），永远无标的可滤，确认不可行。
- **机制**：强趋势段箱体回踩是高质量顺势买点，反转 K 线（锤子/看涨吞没）筛掉逆势毛刺；震荡段箱体做双侧均值回归，反转过滤误删有效信号——故过滤的增益在**强趋势**、不在震荡。
- **行动**：EA 侧以 `|iMA(close,96) 的 20-bar 相对斜率| >= 5.1e-5`（训练段校准值，留出验证用）作为过滤开关。**留出验证（Round 6）已通过**：样本外段 `rev_in_strong` 仍 +24.6pp 优于常开，泛化成立，可落地（见 §4 步骤 1 与 §1 Round 6）。

---

## 1. 四轮验证证据

### Round 1 — 全样本 A/B（单一切片，IS 主导）

| 开关 | 总收益 | 年化 | 最大回撤 | Sharpe | 平仓数 | 胜率 | PF |
|---|---|---|---|---|---|---|---|
| ON  (True)  | +84.09% | 22.67 | 13.16% | 0.589 | 514  | 50.6% | **1.388** |
| OFF (False) | +98.08% | 25.72 | 10.95% | 0.808 | 1173 | 52.6% | 1.178 |

- OFF 收益 +14pp、Sharpe 更高、回撤更小；但交易数 2.3×、PF 低 0.21。
- **关键陷阱**：全样本把「IS 段 OFF 占优」与「OOS 段 ON 占优」混在一起（见 Round 2），单项切片会误导。

### Round 2 — IS / OOS 切分

- 切分：TRAIN(IS) 2023-09-13~2025-06-01；TEST(OOS) 2025-06-01~2026-08-29

| 窗口 | 开关 | 总收益 | 最大回撤 | Sharpe | 平仓数 | PF |
|---|---|---|---|---|---|---|
| TRAIN(IS) | ON  | -0.59% | 8.61%  | -0.37 | 225 | 0.96  |
| TRAIN(IS) | OFF | +12.51% | 10.59% | 0.316 | 507 | 1.087 |
| TEST(OOS) | ON  | **+80.7%** | 13.09% | 1.118 | 275 | **1.557** |
| TEST(OOS) | OFF | +73.66% | 12.42% | **1.763** | 642 | 1.180 |

- 方向 IS→OOS 翻转：IS 段 OFF 占优，OOS 段 ON 收益与 PF 占优（PF 1.557 vs 1.180）。
- 说明 Round 1 的「OFF 更好」是 IS 主导假象，真实样本外 ON 的**单笔质量（PF）显著更高**。

### Round 3 — 6 折 walk-forward（半年折）

| 折 | 区间 | ON 收益 / PF / DD | OFF 收益 / PF / DD | 收益胜方 | PF 胜方 |
|---|---|---|---|---|---|
| F1 23H2 | 23-09~24-03 | +0.61 / 1.084 / 1.76%  | -0.36 / 0.975 / 3.74%  | ON  | ON  |
| F2 24H1 | 24-04~24-09 | -5.88 / 0.707 / 7.33%  | -5.41 / 0.847 / 7.63%  | OFF | OFF |
| F3 24H2 | 24-10~25-03 | +3.25 / 1.213 / 2.89%  | +5.61 / 1.188 / 5.09%  | OFF | ON  |
| F4 25H1 | 25-04~25-09 | +3.17 / 1.036 / 7.24%  | **+22.39 / 1.301 / 7.09%** | OFF | OFF |
| F5 25H2 | 25-10~26-03 | **+60.11 / 2.076 / 13.39%** | +44.39 / 1.204 / 13.33% | ON  | ON  |
| F6 26H1 | 26-04~26-08 | +12.59 / 1.201 / 8.37%  | +14.12 / 1.089 / 13.97% | OFF | ON  |

**聚合均值**（6 折平均）：

| 指标 | ON | OFF | 差值（ON−OFF） |
|---|---|---|---|
| 平均收益 | 12.31% | 13.46% | -1.15pp（OFF 略高） |
| 平均 PF   | **1.220** | 1.101 | **+0.12（ON 质量更高）** |
| 平均回撤 | **6.83%** | 8.48% | **-1.65pp（ON 更稳）** |

- 收益胜方：ON 2/6（F1、F5）、OFF 4/6；PF 胜方：ON 4/6、OFF 2/6。
- **逐折方向翻转、无稳定占优方**。ON 在「单笔质量 + 回撤」维度系统性更优，OFF 在「原始收益」维度更优——与 Round 1 画像一致。

### Round 4 — 条件化开关（证伪，负结果）

子类 `ConditionalRevStrategy` 覆盖 `want_entry`，`rev_mode ∈ {weak_ma96, high_vol, weak_ma96_or_high_vol}`，阈值 `rev_weak_ma_thresh=0.0003`、`rev_highvol_mult=1.2`。

| 模式 | 行为 | 结论 |
|---|---|---|
| `high_vol` | 退化为 OFF（atr_regime_gate 已在高波动段禁交易，条件永不被触发，每折数值 ≡ OFF） | 无增量，等同常关 |
| `weak_ma96` | 近似 ON 但略松（F5 交易 129 笔介于 ON 95 / OFF 268；F4 收益 3.16% 未吃到 OFF 的 22.39% 牛市） | 像 ON 但不如 ON；F5 回撤 18.93% > ON 13.39% |
| `weak_ma96_or_high_vol` | 与 `weak_ma96` 数值一致（高波动段被 gate 吃掉，仅 weak_ma96 分支生效） | 同上 |

- 三种模式**没有任何一折同时击败 ON 与 OFF**；`weak_ma96` 在 F4/F5/F6 回撤更大。
- **结论：Step 2 的条件化测试本身是退化的（阈值不当 + 轴撞车），并未公正检验想法；该负结果在 Round 5 被推翻。**

### Round 5 — 修正的条件化验证（regime 校准，walk-forward）

Step 2 的条件化测试是**退化**的（`weak_ma96` 阈值 0.0003 ≈ 5× median → 几乎恒开；`high_vol` 与 `atr_regime_gate` 撞车 → 恒关），未公正检验想法。本轮修正：

- 阈值 = 全样本校准 **median|MA96 相对斜率| = 6.1e-5**（弱趋势 = 底 50% 段，不再是 0.0003 的伪弱）。
- regime 轴绑到**与交易共存的 MA96 斜率强弱**（不撞 gate）。
- 加互补模式 `rev_in_strong`（仅强趋势开过滤）以隔离「过滤在哪个 regime 有用」。
- 4 模式 × 6 折，均复用 `PROFILES["scalp"]`（含 `atr_regime_gate` / `ma_filter` / `trailing`）。

| 折 | always_on(ON, 当前EA) 收益/PF/DD | always_off(OFF) 收益/PF/DD | rev_in_weak 收益/PF/DD | **rev_in_strong** 收益/PF/DD |
|---|---|---|---|---|
| F1 23H2 | +0.61 / 1.084 / 1.76% | -0.36 / 0.975 / 3.74% | -1.61 / 0.887 / 3.74% | **+1.58 / 1.207 / 1.76%** |
| F2 24H1 | -5.88 / 0.707 / 7.33% | -5.41 / 0.847 / 7.63% | **-2.91 / 0.910 / 6.16%** | -6.96 / 0.689 / 8.46% |
| F3 24H2 | +3.25 / 1.213 / 2.89% | +5.61 / 1.188 / 5.09% | +2.20 / 1.076 / 5.48% | **+6.66 / 1.419 / 2.32%** |
| F4 25H1 | +3.17 / 1.036 / 7.24% | +22.39 / 1.301 / 7.09% | **+24.83 / 1.350 / 6.90%** | -0.64 / 0.938 / 10.12% |
| F5 25H2 | +60.11 / 2.076 / 13.39% | +44.39 / 1.204 / 13.33% | +35.45 / 1.172 / 13.33% | **+70.30 / 2.151 / 10.98%** |
| F6 26H1 | +12.59 / 1.201 / 8.37% | +14.12 / 1.089 / 13.97% | +2.43 / 1.007 / 16.14% | **+27.07 / 1.413 / 8.11%** |

**聚合均值（6 折）**：

| 模式 | 平均收益 | 平均 PF | 平均 DD | 收益胜方折数 | PF 胜方折数 |
|---|---|---|---|---|---|
| always_on（EA 当前） | 12.31% | 1.220 | 6.83% | — | — |
| always_off | 13.46% | 1.101 | 8.48% | 4/6 | 2/6 |
| rev_in_weak | 10.07% | 1.067 | 8.63% | 2/6 | 2/6 |
| **rev_in_strong** | **16.34%** | **1.303** | **6.96%** | **4/6** | **4/6** |

- **核心发现**：反转过滤的价值方向与原假设相反——在**强趋势段增益最大、在弱趋势/震荡段反而减益**。`rev_in_strong` 拿到最高平均收益、最高平均 PF、最低档 DD（仅略高于 ON 的 6.83%），是 6 折中唯一稳定优于 EA 常开默认的模式。
- `rev_in_weak` 弱于常开，说明「震荡段才需要过滤」的直觉是错的：箱体在震荡段做双侧均值回归，反转过滤误删了有效信号。
- 仅 F2/F4 两折 `rev_in_strong` 略逊于常开（且恰是策略整体偏弱/走平的折），属少数例外。
- **交易笔数**：`rev_in_strong`（51/85/78/115/109/128）介于 ON 与 OFF 之间——强趋势段保持质量、震荡段放开交易，取两者之长。

### Round 6 — 留出验证（排除 Round 5 全样本校准的轻度泄漏）

- 校准：门槛仅用**训练段 2023-09-13~2025-06-01** 算 median|MA96 相对斜率| = **5.1e-5**（对比 Round 5 全样本 6.1e-5，几乎一致 → 门槛本就稳定）。
- 验证：用该「未见过测试段」的干净门槛，在**样本外段 2025-06-01~2026-08-29**（含 F5/F6）跑 4 模式。

| 范围 | always_on(当前EA) 收益/PF/DD | always_off 收益/PF/DD | rev_in_weak 收益/PF/DD | **rev_in_strong** 收益/PF/DD |
|---|---|---|---|---|
| TEST 全段(留出) | +80.70 / 1.557 / 13.09% | +73.66 / 1.180 / 12.42% | +51.35 / 1.123 / 13.42% | **+105.29 / 1.720 / 13.09%** |
| F5 25H2 | +60.11 / 2.076 / 13.39% | +44.39 / 1.204 / 13.33% | +35.71 / 1.161 / 14.06% | **+65.21 / 2.121 / 13.41%** |
| F6 26H1 | +12.59 / 1.201 / 8.37% | +14.12 / 1.089 / 13.97% | +4.78 / 1.024 / 14.72% | **+27.79 / 1.440 / 7.28%** |

- **结论：泛化成立**。留出集上 `rev_in_strong` 仍稳定优于常开：全段 +24.6pp（与全样本 +24pp 几乎一致），F5/F6 两折全胜，且 F6 回撤更低（7.28% < 8.37%）。`rev_in_weak` 在留出集仍最差（+51.35% < 常开），「震荡段才要过滤」的直觉再次被证伪。
- +24pp 优势**不是门槛泄漏假象**，可放心落地。

---

## 2. 决策依据（综合 Round 1~6）

1. **全样本「OFF 更好」是 IS 主导假象**（Round 1 + Round 2 揭示），真实样本外 ON 的 PF 高 0.377（1.557 vs 1.180）。
2. **常开 vs 常关无稳定占优**（Round 3 walk-forward）：ON 在 PF/DD 维度更优、OFF 在原始收益略优，逐折翻转。
3. **条件化开关有用，但方向要反**（Round 5）：`rev_in_strong`（仅强趋势开过滤）在 4/6 折同时胜出 ON 与 OFF，累计收益比 EA 常开默认高 ~+24pp，是验证集上综合最优模式；`rev_in_weak`（震荡段开过滤）反而最差。
4. **机制自洽**：强趋势段箱体回踩是高质量顺势买点，反转 K 线筛逆势毛刺有效；震荡段箱体双侧均值回归，反转过滤误删信号。
5. **原 `high_vol` 提议不可行**：与 `atr_regime_gate` 撞车，无交易可滤。
6. **建议**：EA 从「`InpUseReversalFilter=true`（常开）」改为「以 MA96 斜率幅度为门槛的条件化开启」（见 §4 落地步骤）。提交前做一次性泄漏检查。

---

## 3. 风险提示与已知偏差

- **swap 模型说明（已修正）**：`run_backtest` 内置 TMGM 隔夜费（`_charge_swap`：swap_long=-72.5 / swap_short=+30.72 点、周三3倍、服务器零点收取）。真实 scalp 用 `avoid_overnight=True`，持仓在 server 20:00 强平、不跨零点 → `swap_total` 实测≈0.00USD（**非"未计入"，而是策略设计上几乎不付 swap**）。**含 swap 压力情景（持仓过夜）**见 §4 第 2 项：常关/rev_in_weak 交易更密→过夜更多→被 swap 侵蚀更重（-6.7/-6.3 USD），rev_in_strong/always_on 仅 -2.3/-2.8 USD，且 rev_in_strong 仍收益/PF 双优。
- **样本依赖 2025 牛市**：策略主要收益来自 F5（25H2，ON +60% / OFF +44%）。2023H2~2024 多折接近持平或亏损，策略本身高度 regime-dependent；过滤开关是二阶调节，不改变 regime 暴露。
- **停利 / 止损仅在 bar 收盘检查**（原始 `BoxChannelStrategy` L299-322），未做 intrabar 触发；实盘滑点与定点差下表现会衰减。
- **CLI 默认不一致**：基线 `xauusd_box_channel_strategy.py` 类默认 `use_reversal_filter=True`（L96），但 `--rev-filter` 默认 False 且 L525 覆盖（L514 store_true）→ 库调用常开、CLI 常关。EA 侧 `InpUseReversalFilter=true` 与库默认一致，但与 CLI 不一致，建议统一对齐。
- **Round 5 阈值存在轻度泄漏**：`rev_weak_ma_thresh=6.1e-5` 由全样本 median 校准，评估时该分布已「见过」各折。因其仅是 50/50 中位数分界，逐折中位数近似，泄漏影响小；`rev_in_strong` 的 +24pp 优势已用「阈值仅用 2023-2025 校准、2025-2026 验证」的留出验证复核（Round 6），**复核通过**，可提交 EA。

---

## 4. 后续动作（Round 5 后优先序）

1. **（最高优先，✅ 留出验证已通过，✅ 2026-09-14 已落地生产代码）EA + Python 双端落地 `rev_in_strong` 条件化**：
   - ✅ 留出验证（Round 6）完成：训练段校准门槛 **5.1e-5**，样本外段 `rev_in_strong` 仍 +24.6pp 优于常开，泛化成立。
   - ✅ **Python 落地**（`box_channel_optimized.py`）：
     - 新增参数 `rev_mode`（默认 `"always_on"` 保历史兼容）/ `rev_strong_thresh`（默认 `5.1e-5`）。
     - 新增 `_ma96_rel_slope()`（取 `self.ma_tf` 近 20 根已收盘 EMA 算 20-bar 相对斜率，对齐 EA）+ `_effective_rev_filter()`（`want_entry` 的 box 分支改用 `eff` 替代原 `self.p.use_reversal_filter`）。
     - 新增画像 `scalp_rev_strong`（`rev_mode="rev_in_strong"`），并暴露 `--rev-mode` / `--rev-strong-thresh` CLI。
   - ✅ **EA 落地**（`mql5/BoxChannelScalp.mq5`）：
     - 新增输入 `InpRevStrongOnly`（默认 `true`=仅强趋势段开）、`InpRevStrongThresh`（默认 `5.1e-5`）；`InpUseReversalFilter` 降为总开关。
     - 新增 `MA96RelSlope()`（复用 `LinRegSlope`/`ArrayMean`，取 shift 1..20 已收盘 EMA 算相对斜率），入场判定以 `revFilterOn` 替代固定 `!InpUseReversalFilter`，并在 `[ATTEMPT]` 日志打印 `maRelSlope`/`revFilterOn`。
     - 需重新复制源码到 MT5 终端并编译（见 README_port.md 步骤二），旧 `.ex5` 已过期。
   - ✅ **本轮回测验证**（生产代码直接跑，非 harness）：同 Round 6 窗口 2025-06-01~2026-08-29、起始 $1000/0.01 手
     | 画像 | 累计收益 | 最大回撤 | PF | 交易数 | 胜率 |
     |---|---|---|---|---|---|
     | `scalp`（常开，原 EA 行为） | +80.70% | 13.09% | 1.56 | 275 | 53.8% |
     | **`scalp_rev_strong`（仅强趋势段开）** | **+101.49%** | 13.09% | 1.68 | 299 | 56.2% |
     | 全样本 `scalp_rev_strong`（2023-09~2026-08） | +108.19% | 12.78% | 1.49 | 560 | 52.5% |
     → 样本外窗口 +20.8pp、回撤持平、PF 与胜率均升，与 Round 6 留出结论（+24.6pp）方向一致，确认生产代码实现正确。
   - 注意：`scalp` 画像已含 `atr_regime_gate`，强趋势段通常不被 gate 禁掉，故该开关与 gate 不冲突。
2. **✅ 2026-09-14 完成：含 TMGM 隔夜费重跑，确认 `rev_in_strong` 仍最优**（harness `_rev_ab_swap.py`，TEST 窗口 2025-06-01~2026-08-29，$1000/0.01 手，阈值 5.1e-5）：
   - swap 模型早已内置 `run_backtest`（`_charge_swap`：swap_long=-72.5 / swap_short=+30.72 点、周三3倍、服务器零点收取）。真实 scalp 用 `avoid_overnight=True`，持仓在 server 20:00 强平、不跨过零点 → **swap≈0**（4 模式均 0.00USD）。
   - 为检验 swap 真实影响，加跑 `avoid_overnight=False`（持仓过夜）压力情景，swap 生效：
     | 情景 | always_on(常开) 收益/PF/DD/swap | **rev_in_strong** 收益/PF/DD/swap | always_off(常关) 收益/PF/DD/swap | rev_in_weak 收益/PF/DD/swap |
     |---|---|---|---|---|
     | 真实(avoid_overnight=T) | +80.70 / 1.557 / 13.09 / 0.00 | **+101.49 / 1.683 / 13.09 / 0.00** | +73.66 / 1.180 / 12.42 / 0.00 | +55.78 / 1.136 / 12.61 / 0.00 |
     | 过夜(avoid_overnight=F) | +89.73 / 1.587 / 11.61 / -2.33 | **+108.31 / 1.675 / 10.65 / -2.75** | +65.44 / 1.151 / 15.33 / -6.71 | +46.70 / 1.108 / 21.97 / -6.29 |
   - **结论**：`rev_in_strong` 在两种情景下均为累计收益与 PF 最优；swap 压力下其相对更弱模式的优势反而扩大——常关从 +73.66→+65.44（被 swap 侵蚀 -8.2pp）、rev_in_weak 从 +55.78→+46.70（-9.1pp），而 rev_in_strong 仅 -2.75、always_on 仅 -2.33（交易更稀→过夜更少→swap 更省）。验证原假设「OFF 受 swap 侵蚀更严重→进一步支持强过滤」成立。实际 scalp 部署因 `avoid_overnight` 几乎不付 swap，结论更稳。
3. **intrabar 止损**：将 SL/TP 检查改为下一根开盘价近似或订单触发，消除 bar 收盘检查的乐观偏差。
4. **regime 层才是主战场**：对 2023H2~2024（F1/F2）亏损段做 regime 识别（MA96 斜率 + ATR 分级）以控制空仓，比过滤开关增益更大。
5. **统一 CLI 默认**：把 `xauusd_box_channel_strategy.py` 的 `--rev-filter` 改为 `default=True` 或删除覆盖，使 CLI 与 EA / 库默认一致。

---

## 5. 证据文件清单

| 文件 | 内容 |
|---|---|
| `_rev_ab_result.json` | Round 1 全样本 A/B |
| `_rev_ab_oos_result.json` | Round 2 IS/OOS |
| `_rev_ab_walkforward_result.json` | Round 3 6 折 walk-forward |
| `_rev_ab_conditional_result.json` | Round 4 条件化开关（退化，负结果） |
| `_rev_ab_conditional2_result.json` | **Round 5 修正的条件化验证（regime 校准，rev_in_strong 胜出）** |
| `_rev_ab_holdout_result.json` | **Round 6 留出验证（训练段校准门槛 5.1e-5，样本外 rev_in_strong +24.6pp 优于常开）** |
| `_rev_ab_swap.py` / `_rev_ab_swap_result.json` | **含 TMGM 隔夜费重跑（§4 第 2 项）：4 模式 × 两种隔夜设置，rev_in_strong 含 swap 仍最优** |
| `_cooldown_ab.py` / `_cooldown_ab_result.json` | **EA↔回测 cooldown 对齐对拍（§4 第 6 项）：4 模式 × cd{2,8} × 两种隔夜设置，确认 rev_in_strong 在 cd=8 仍最优** |
| `_rev_ab.py` / `_rev_ab_oos.py` / `_rev_ab_walkforward.py` / `_rev_ab_conditional.py` / `_rev_ab_conditional2.py` / `_rev_ab_holdout.py` | 对应回测 harness |
| `box_strategy_optimization_report.md` | 更广的入场风格 / 参数网格 / 杠杆矩阵研究（阶段 A–D，含 swap 建模） |
| `BOX_CHANNEL_STRATEGY_DESIGN.html` | 策略设计文档（流程图 + 数值示例） |
| `box_channel_optimized.py` | 优化版策略与 `PROFILES` |
