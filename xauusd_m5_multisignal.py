# -*- coding: utf-8 -*-
"""
XAUUSD M5 高频多信号 Scalping 策略
=================================
目标: 相对现有 15m box 策略(约 0.5 笔/天)大幅提升交易频率, 做到"每天都有成交",
      并用全面技术面(斜率/强支撑压力/锚点/均线/RSI/TICKVOL)提高胜率。

架构:
  data0 = 5m  (主时钟, 必须第一个 adddata)
  data1 = 15m (上下文: ADX 趋势强度)
  data2 = 1h  (上下文: 通道斜率 / EMA 位置 → regime)

三族信号(靠 regime 路由避免互斗, 同 bar 只出 1 个):
  A 趋势回踩   : TREND_UP 只多 / TREND_DOWN 只空
  B 突破动量   : 顺势突破 + 量能确认 (+ 假突破反手 B')
  C 震荡回归   : 仅 RANGE, 区间沿反转

关键约定(继承自 box_channel_optimized 的既有铁律):
  - notify_order 保持 pass(本 backtrader 构建订单身份不匹配) → 在 next() 轮询 .status
  - SL/TP 用 broker 端 Stop/Limit 挂单(盘中触发, M5 必需)
  - 所有距离参数用 ATR 相对值(样本内 ATR 跨 4.5 倍, 美元定值不可移植)
  - 日界/时段一律由 bt.num2date() 推导(服务器 0 点无 bar, 禁止按 bar 根数推算)

用法:
  python xauusd_m5_multisignal.py --from 2025-04-15 --to 2026-09-11
"""
import os
import sys
from collections import defaultdict, Counter

import numpy as np
import pandas as pd
import backtrader as bt

from box_channel_optimized import (
    BoxChannelOptStrategy, FixedLotSizer, load_data, tmgm_server_offset_hours,
)

# ============================ 工具函数 ============================


def ols_fit(xs, ys):
    """闭式 OLS: 返回 (slope, intercept, r2)。xs/ys 等长。"""
    n = len(xs)
    if n < 3:
        return 0.0, (ys[-1] if ys else 0.0), 0.0
    xm = sum(xs) / n
    ym = sum(ys) / n
    num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    den = sum((x - xm) ** 2 for x in xs)
    if den <= 0:
        return 0.0, ym, 0.0
    b = num / den
    a = ym - b * xm
    ss_tot = sum((y - ym) ** 2 for y in ys)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return b, a, r2


def server_hour(dt, offset):
    """UTC datetime → TMGM 服务器小时。"""
    return (dt.hour + offset) % 24


def bars_for_minutes(minutes, bar_minutes):
    """把'分钟'口径的回看窗口换算成 bar 根数(跨周期可移植的前提)。"""
    return max(2, int(round(minutes / float(bar_minutes))))


# ============================ 策略主体 ============================

class M5MultiSignalStrategy(BoxChannelOptStrategy):
    params = dict(
        # ---------- regime router ----------
        rel_th=0.0006,            # 1h 通道斜率阈值(斜率/均值)
        adx_th=20.0,              # 15m ADX 趋势阈值
        regime_confirm_bars=3,    # regime 切换需连续确认根数(滞回)
        atr_lo=0.6, atr_hi=2.0,   # 波动率 regime 上下阈(atr/atr_avg)
        shock_freeze_bars=6,      # VOL_SHOCK 后冻结根数
        range_rel_mult=0.6,       # RANGE 判定: |rel| < rel_th × 此值 (调大=更容易判为震荡→解锁 C 族)
        allow_B_in_transition=False,  # TRANSITION 状态是否允许 B(突破)
        enable_A=True, enable_B=True, enable_C=True,
        allow_in_transition=True,
        # ---------- A 趋势回踩 ----------
        pb_tol_atr=0.30,          # 回踩容差: |c - 支撑| <= tol × atr
        pb_min_depth_atr=0.25,    # 最小回撤深度(防没回调就进)
        rsi_pb_lo=35.0, rsi_pb_hi=55.0,
        k_A=1.5,                  # A 族 R 倍数
        atr_stop_mult_A=1.1,      # A 族止损 ATR 倍数(兜底)
        stop_min_atr=0.9,         # A 族止损最小距离(×ATR); 太小易被 5m 噪声扫掉
        stop_max_atr=1.6,         # 止损距离上限, 超过则放弃(层太远)
        # ---------- B 突破动量 ----------
        dc_minutes=120,           # 唐奇安回看(分钟口径, 跨周期可移植)
        bo_buf_atr=0.10,          # 突破缓冲
        vol_mult=1.3,             # TICKVOL 倍数确认
        rsi_bo_lo=50.0, rsi_bo_hi=75.0,
        k_B=2.2,
        enable_fade=True, fade_bars=2,   # 假突破反手
        # ---------- C 震荡回归 ----------
        rg_minutes=360,           # 区间回看(分钟口径)
        rg_span_max_atr=6.0,      # 区间最大跨度(超过不算震荡); 实测 6h 区间常达 4~6 ATR
        fade_tol_atr=0.10,
        k_C=1.2,
        atr_stop_mult_C=0.5,
        # ---------- 共享 TA ----------
        vol_ma_period=20,
        prox_atr=0.25,            # "近层"判定: |c - level| <= prox × atr
        tol_atr=0.15,             # 触点判定容差
        strong_touches=3,         # 强支撑压力最少触点
        pivot_len=3, slope_k=4, slope_r2=0.70,
        bar_minutes=5,            # 主周期分钟数(用于分钟→根数换算)
        # ---------- 过滤 / 风控 ----------
        quality_gate=True,        # 逐 bar 质量(波幅 or 量能)
        bar_range_min_atr=0.5,
        vol_ratio_min=0.8,
        max_spread_tail=20.0,     # 点差只挡尾部异常(points)
        min_risk_usd=3.0,         # 最小风险(美元, 成本底线)
        min_target_usd=2.0,       # 最小目标(美元, >= 6×往返成本 0.33)
        max_trades_day=8,
        max_daily_loss_r=2.5,
        max_hold_bars=24,         # 时间止损(根)
        cooldown_bars=6, cooldown_after_loss=12, cooldown_after_win=3,
        # ---------- 出场 ----------
        trail_activate_atr=0.8, trail_atr_mult=0.9, trail_be_buffer=0.15,
        atr_shift=1,              # 用上一根已收盘 bar 的 ATR(对齐 EA GetATRVal(1))
        eod_lead_hours=2,         # EOD 提前小时(server_close_hour 之前)
        no_trade_before="",       # 'YYYY-MM-DD': 之前只预热指标不开仓(用于 walk-forward 分段统计)
        print_rejects=False,      # 拒绝原因漏斗(调试用)
    )

    # ------------------------------------------------------------------ 初始化
    def __init__(self):
        super().__init__()
        d = self.data
        # 5m 指标(父类已建 rsi / atr / ma_fast(EMA5) / ma_slow(EMA20) / ma_tf(EMA96) / atr_avg)
        self.ema50_5m = bt.indicators.EMA(d.close, period=50)
        self.vol_ma = bt.indicators.SMA(d.volume, period=self.p.vol_ma_period)
        # 上下文: 15m ADX / 1h EMA20
        self.adx_15m = bt.indicators.ADX(self.datas[1], period=14)
        self.ema20_1h = bt.indicators.EMA(self.datas[2].close, period=20)

        # regime 缓存(仅在 15m/1h 长度变化时重算)
        self._regime_key = None
        self._regime = "TRANSITION"
        self._regime_cand = None
        self._regime_cnt = 0
        self._rel_1h = 0.0

        # 拒绝原因漏斗
        self.rejects = Counter()

        # 日内/日级状态(按 UTC 日期, 由时间戳推导)
        self._day = None
        self._day_high = -np.inf
        self._day_low = np.inf
        self._day_open = None
        self._prev_day = None          # dict(high, low, close)
        self._trades_today = 0
        self._pnl_r_today = 0.0
        # 开盘区间 OR(服务器时 10-11 / 15-16)
        self._or_london = None
        self._or_ny = None

        # 冷却/持仓计时
        self._last_entry_bar = -9999
        self._last_entry_side = None
        self._hold_bars = 0
        self._shock_until = -1
        self._pending_cooldown = int(self.p.cooldown_bars)
        # 预热截止日(walk-forward 分段统计用)
        self._no_trade_before = (pd.Timestamp(self.p.no_trade_before).date()
                                 if self.p.no_trade_before else None)

    # ------------------------------------------------------------------ 辅助
    def _rej(self, k):
        self.rejects[k] += 1
        if self.p.print_rejects:
            print(f"[REJ] {k}")

    def _now_dt(self):
        return bt.num2date(self.data.datetime[0])

    def _server_hour(self, dt):
        return server_hour(dt, tmgm_server_offset_hours(dt))

    def _atr_now(self):
        return self.atr[-self.p.atr_shift]

    def _roll_day(self, dt):
        """新交易日: 归档前一日 OHLC, 重置日内计数与 OR。"""
        d = dt.date()
        if self._day == d:
            return
        if self._day is not None and self._day_high > -np.inf:
            self._prev_day = dict(high=self._day_high, low=self._day_low,
                                  close=self._day_close, open=self._day_open)
        self._day = d
        self._day_high = -np.inf
        self._day_low = np.inf
        self._day_open = self.data.open[0]
        self._day_close = self.data.close[0]
        self._trades_today = 0
        self._pnl_r_today = 0.0
        self._or_london = None
        self._or_ny = None

    def _update_day_ohlc(self):
        self._day_high = max(self._day_high, self.data.high[0])
        self._day_low = min(self._day_low, self.data.low[0])
        self._day_close = self.data.close[0]

    def _update_or(self, hr):
        """维护伦敦(10-11)与纽约(15-16)开盘区间。"""
        hi, lo = self.data.high[0], self.data.low[0]
        if 10 <= hr < 11:
            if self._or_london is None:
                self._or_london = [hi, lo]
            else:
                self._or_london[0] = max(self._or_london[0], hi)
                self._or_london[1] = min(self._or_london[1], lo)
        if 15 <= hr < 16:
            if self._or_ny is None:
                self._or_ny = [hi, lo]
            else:
                self._or_ny[0] = max(self._or_ny[0], hi)
                self._or_ny[1] = min(self._or_ny[1], lo)

    # ------------------------------------------------------------------ regime
    def _compute_regime(self):
        key = (len(self.datas[1]), len(self.datas[2]))
        if key == self._regime_key:
            return self._regime
        self._regime_key = key
        # 1h 通道斜率(只取已收盘 1h bar, 避免未来函数)
        try:
            ys = [self.datas[2].close[-i] for i in range(24, 0, -1)]
        except Exception:
            ys = []
        if len(ys) >= 10 and min(ys) > 0:
            mean = sum(ys) / len(ys)
            b, a, _ = ols_fit(list(range(len(ys))), ys)
            rel = (b / mean) if mean else 0.0
        else:
            rel = 0.0
        self._rel_1h = rel
        # 15m ADX(已收盘)
        try:
            adx = self.adx_15m[-1]
        except Exception:
            adx = 0.0
        adx = adx if adx == adx else 0.0  # NaN
        # 1h 收盘 vs EMA20
        try:
            c1h = self.datas[2].close[-1]
            e1h = self.ema20_1h[-1]
        except Exception:
            c1h, e1h = 0.0, 0.0
        # 波动率
        atr = self._atr_now()
        atr_avg = self.atr_avg[-self.p.atr_shift] if len(self.atr_avg) else 0.0
        ratio = (atr / atr_avg) if atr_avg and atr_avg > 0 else 1.0

        if ratio > self.p.atr_hi:
            cand = "VOL_SHOCK"
        elif rel > self.p.rel_th and c1h > e1h and adx > self.p.adx_th:
            cand = "TREND_UP"
        elif rel < -self.p.rel_th and c1h < e1h and adx > self.p.adx_th:
            cand = "TREND_DOWN"
        elif abs(rel) < self.p.rel_th * self.p.range_rel_mult and adx < self.p.adx_th:
            cand = "RANGE"
        else:
            cand = "TRANSITION"

        # 滞回确认
        if cand == self._regime_cand:
            self._regime_cnt += 1
        else:
            self._regime_cand = cand
            self._regime_cnt = 1
        if self._regime_cnt >= self.p.regime_confirm_bars:
            self._regime = cand
        return self._regime

    # ------------------------------------------------------------------ 支撑压力
    def _strong_levels(self, atr):
        """强支撑/压力: 用父类 cluster_levels 找候选层, 再按触点数过滤。"""
        lv = []
        try:
            s15, r15 = self.cluster_levels(self.datas[1], 96)
            if s15 and s15 > 0:
                lv.append(s15)
            if r15 and r15 > 0:
                lv.append(r15)
        except Exception:
            pass
        out = []
        tol = self.p.tol_atr * atr
        for L in lv:
            if L <= 0:
                continue
            # 触点统计(15m 最近 96 根)
            touches = 0
            for i in range(1, min(97, len(self.datas[1]))):
                lo = self.datas[1].low[-i]
                hi = self.datas[1].high[-i]
                if lo - tol <= L <= hi + tol:
                    touches += 1
            if touches >= self.p.strong_touches:
                out.append(L)
        return sorted(out)

    def _pivots(self, kind="low", lookback=200):
        """已确认 pivot(kind=low/high), 返回 [(bars_ago, price), ...]。"""
        pl = int(self.p.pivot_len)
        out = []
        limit = min(lookback, max(0, len(self.data) - 1))
        for k in range(pl, limit):
            try:
                v = self.data.low[-k] if kind == "low" else self.data.high[-k]
            except Exception:
                break
            ok = True
            for j in range(-k - pl, -k + pl + 1):
                if j == -k or j > 0:
                    continue
                w = self.data.low[j] if kind == "low" else self.data.high[j]
                if kind == "low" and w < v:
                    ok = False
                    break
                if kind == "high" and w > v:
                    ok = False
                    break
            if ok:
                out.append((k, v))
        return out

    def _slope_level(self, kind="low", want_sign=1):
        """斜率支撑/压力: 对最近 k 个 pivot 做 OLS, 返回当前线值(失败返回 None)。"""
        pts = self._pivots(kind)[: self.p.slope_k]
        if len(pts) < 3:
            return None
        xs = [-k for k, _ in pts]        # x 越大越靠近现在, x=0 为当前
        ys = [p for _, p in pts]
        b, a, r2 = ols_fit(xs, ys)
        if r2 < self.p.slope_r2:
            return None
        if (b > 0) != (want_sign > 0):
            return None
        return a  # 当前线值

    # ------------------------------------------------------------------ 形态
    def _reversal_bull(self, level, atr):
        """近层看涨反转(锤子/看涨吞没/pin)。"""
        try:
            o, h, l, c = self.data.open[-1], self.data.high[-1], self.data.low[-1], self.data.close[-1]
        except Exception:
            return False
        rng = h - l
        if rng <= 0:
            return False
        near = abs(c - level) <= self.p.prox_atr * atr if level else False
        lower = min(o, c) - l
        hammer = (lower / rng >= 0.55) and (c >= o)
        po = self.data.open[-2]
        pc = self.data.close[-2]
        engulf = (pc < po) and (c > o) and (c > po)
        return near and (hammer or engulf)

    def _reversal_bear(self, level, atr):
        try:
            o, h, l, c = self.data.open[-1], self.data.high[-1], self.data.low[-1], self.data.close[-1]
        except Exception:
            return False
        rng = h - l
        if rng <= 0:
            return False
        near = abs(c - level) <= self.p.prox_atr * atr if level else False
        upper = h - max(o, c)
        star = (upper / rng >= 0.55) and (c <= o)
        po = self.data.open[-2]
        pc = self.data.close[-2]
        engulf = (pc > po) and (c < o) and (c < po)
        return near and (star or engulf)

    # ------------------------------------------------------------------ 过滤
    def _pass_filters(self, hr, atr):
        if self.p.session_filter:
            sh = self._effective_start_hour()
            if hr < sh or hr >= self.p.trade_end_hour:
                self._rej("session")
                return False
        if len(self) <= self._shock_until:
            self._rej("shock_freeze")
            return False
        # 波动率 regime
        atr_avg = self.atr_avg[-self.p.atr_shift] if len(self.atr_avg) else 0.0
        ratio = (atr / atr_avg) if atr_avg and atr_avg > 0 else 1.0
        if ratio < self.p.atr_lo or ratio > self.p.atr_hi:
            self._rej("vol_gate")
            return False
        # 点差(仅尾部)
        try:
            sp = self.current_spread_points(self._now_dt())
            if sp and sp > self.p.max_spread_tail:
                self._rej("spread")
                return False
        except Exception:
            pass
        # 逐 bar 质量
        if self.p.quality_gate:
            rng = self.data.high[0] - self.data.low[0]
            vr = (self.data.volume[0] / self.vol_ma[0]) if self.vol_ma[0] and self.vol_ma[0] > 0 else 0.0
            if not (rng >= self.p.bar_range_min_atr * atr or vr >= self.p.vol_ratio_min):
                self._rej("quality_gate")
                return False
        # 冷却
        if len(self.data) - self._last_entry_bar < self._cur_cooldown():
            self._rej("cooldown")
            return False
        # 日内上限
        if self._trades_today >= self.p.max_trades_day:
            self._rej("max_trades_day")
            return False
        if self._pnl_r_today <= -abs(self.p.max_daily_loss_r):
            self._rej("daily_loss")
            return False
        return True

    def _effective_start_hour(self):
        """服务器交易起点(沿用父参数, 冬令 -1 对齐北京 6:00)。"""
        dt = self._now_dt()
        off = tmgm_server_offset_hours(dt)
        return self.p.trade_start_hour if off >= 3 else self.p.trade_start_hour - 1

    def _cur_cooldown(self):
        return int(getattr(self, "_pending_cooldown", self.p.cooldown_bars))

    # ---------------- 开仓/挂 SL-TP: 用"实际成交价"重建止损止盈 ----------------
    def _open_position(self, want, entry_ref, stop, target, rm, rsi):
        super()._open_position(want, entry_ref, stop, target, rm, rsi)
        # 记录"意图风险距离"。市价单在下一根开盘成交, 成交价与信号价会错位;
        # 若沿用按信号价算出的 stop, 错位后可能落在入场价的错误一侧 → 立刻被打掉。
        self.trade_info["risk"] = abs(entry_ref - stop)

    def _attach_sl_tp(self):
        info = self.trade_info
        fill = self.position.price if self.position else info.get("entry")
        risk = info.get("risk")
        k = info.get("r_mult")
        d = info.get("dir")
        if fill and risk and risk > 0 and k and d in (1, -1):
            # 以实际成交价为基准重建 SL/TP, 保持 R 距离与盈亏比不变(根除止损反向)
            if d == 1:
                info["stop"] = fill - risk
                info["target"] = fill + k * risk
            else:
                info["stop"] = fill + risk
                info["target"] = fill - k * risk
        super()._attach_sl_tp()

    # ------------------------------------------------------------------ 信号族
    def _signal_A(self, regime, atr, c):
        """趋势回踩: 返回 (want, entry_ref, stop, target, rm) 或 None。"""
        # 宏观对齐
        try:
            c1h = self.datas[2].close[-1]
            e1h = self.ema20_1h[-1]
        except Exception:
            return None
        # 方向: TREND 由 regime 定; TRANSITION 由 1h 价格 vs EMA20 定(不再硬编码 -1)
        if regime == "TREND_UP":
            direction = 1
        elif regime == "TREND_DOWN":
            direction = -1
        else:
            direction = (1 if (e1h and c1h > e1h) else (-1 if (e1h and c1h < e1h) else 0))
        if direction not in (1, -1):
            self._rej("A_align")
            return None
        if direction == 1 and not (c1h > e1h and self._rel_1h > -self.p.rel_th):
            self._rej("A_align")
            return None
        if direction == -1 and not (c1h < e1h and self._rel_1h < self.p.rel_th):
            self._rej("A_align")
            return None
        # 候选支撑/阻力
        cands = []
        sl = self._slope_level("low" if direction == 1 else "high", want_sign=direction)
        if sl:
            cands.append(sl)
        cands.append(self.ma_slow[0])        # EMA20(5m)
        cands.append(self.ema50_5m[0])
        for L in self._strong_levels(atr):
            cands.append(L)
        pv = self._pivots("low" if direction == 1 else "high", lookback=60)
        if pv:
            cands.append(pv[0][1])
        # 取"最接近且在有利一侧"的层
        tol = self.p.pb_tol_atr * atr
        best = None
        for L in cands:
            if L is None or L <= 0:
                continue
            if direction == 1 and L <= c and (c - L) <= tol:
                if best is None or (c - L) < (c - best):
                    best = L
            elif direction == -1 and L >= c and (L - c) <= tol:
                if best is None or (L - c) < (best - c):
                    best = L
        if best is None:
            self._rej("A_no_pullback")
            return None
        # 回撤深度
        try:
            recent = max([self.data.high[-i] for i in range(2, 26)])
            recent_lo = min([self.data.low[-i] for i in range(2, 26)])
        except Exception:
            return None
        depth = (recent - c) if direction == 1 else (c - recent_lo)
        if depth < self.p.pb_min_depth_atr * atr:
            self._rej("A_depth_fail")
            return None
        # RSI 择时
        rsi = self.rsi[-1]
        if direction == 1 and not (self.p.rsi_pb_lo <= rsi <= self.p.rsi_pb_hi):
            self._rej("A_rsi")
            return None
        if direction == -1 and not (100 - self.p.rsi_pb_hi <= rsi <= 100 - self.p.rsi_pb_lo):
            self._rej("A_rsi")
            return None
        # 反转 K
        if direction == 1 and not self._reversal_bull(best, atr):
            self._rej("A_no_reversal")
            return None
        if direction == -1 and not self._reversal_bear(best, atr):
            self._rej("A_no_reversal")
            return None
        # 风险/目标
        stop = best - 0.25 * atr if direction == 1 else best + 0.25 * atr
        risk = abs(c - stop)
        risk = max(risk, self.p.stop_min_atr * atr)
        if risk > self.p.stop_max_atr * atr:
            self._rej("A_stop_too_far")
            return None
        stop = c - risk if direction == 1 else c + risk
        target = c + self.p.k_A * risk if direction == 1 else c - self.p.k_A * risk
        return (direction, c, stop, target, self.p.k_A)

    def _signal_B(self, regime, atr, c):
        """突破动量(+假突破反手)。"""
        direction = 1 if regime in ("TREND_UP", "VOL_SHOCK") else (-1 if regime == "TREND_DOWN" else 0)
        if direction == 0:
            # RANGE/TRANSITION: 双向突破(由调用方按两侧分别试)
            pass
        nb = bars_for_minutes(self.p.dc_minutes, self.p.bar_minutes)
        # 注意: 必须排除信号 bar(-1)自身, 否则 close[-1] 永远不可能 > 自身 high
        try:
            hi = max([self.data.high[-i] for i in range(2, nb + 2)])
            lo = min([self.data.low[-i] for i in range(2, nb + 2)])
        except Exception:
            return None
        # OR 上沿并入
        box_hi, box_lo = hi, lo
        for OR in (self._or_london, self._or_ny):
            if OR:
                box_hi = max(box_hi, OR[0])
                box_lo = min(box_lo, OR[1])
        buf = self.p.bo_buf_atr * atr
        vr = (self.data.volume[0] / self.vol_ma[0]) if self.vol_ma[0] and self.vol_ma[0] > 0 else 0.0
        rng = self.data.high[0] - self.data.low[0]
        rsi = self.rsi[-1]
        if direction in (1, 0):
            if c > box_hi + buf:
                if vr < self.p.vol_mult or rng < 0.8 * atr:
                    self._rej("B_vol")
                    return None
                if not (self.p.rsi_bo_lo <= rsi <= self.p.rsi_bo_hi):
                    self._rej("B_rsi")
                    return None
                stop = min(c - 1.2 * atr, self.data.low[-1] - 0.2 * atr)
                risk = max(abs(c - stop), 0.6 * atr)
                stop = c - risk
                return (1, c, stop, c + self.p.k_B * risk, self.p.k_B)
        if direction in (-1, 0):
            if c < box_lo - buf:
                if vr < self.p.vol_mult or rng < 0.8 * atr:
                    self._rej("B_vol")
                    return None
                if not (100 - self.p.rsi_bo_hi <= rsi <= 100 - self.p.rsi_bo_lo):
                    self._rej("B_rsi")
                    return None
                stop = max(c + 1.2 * atr, self.data.high[-1] + 0.2 * atr)
                risk = max(abs(stop - c), 0.6 * atr)
                stop = c + risk
                return (-1, c, stop, c - self.p.k_B * risk, self.p.k_B)
        self._rej("B_no_break")
        return None

    def _signal_C(self, regime, atr, c):
        """震荡均值回归(仅 RANGE)。"""
        nb = bars_for_minutes(self.p.rg_minutes, self.p.bar_minutes)
        # 同上: 排除信号 bar 自身, 否则区间沿恒等于信号 bar 的高低点
        try:
            hi = max([self.data.high[-i] for i in range(2, nb + 2)])
            lo = min([self.data.low[-i] for i in range(2, nb + 2)])
        except Exception:
            return None
        span = hi - lo
        if span < 1.5 * atr or span > self.p.rg_span_max_atr * atr:
            self._rej("C_span")
            return None
        tol = self.p.fade_tol_atr * atr
        rsi = self.rsi[-1]
        # 摸底做多
        if self.data.low[-1] <= lo + tol and c > lo:
            if self._reversal_bull(lo, atr) or rsi < 30:
                stop = lo - self.p.atr_stop_mult_C * atr
                risk = max(abs(c - stop), 0.4 * atr)
                stop = c - risk
                mid = (hi + lo) / 2.0
                t_struct = min(hi - 0.2 * atr, mid) if mid > c else hi - 0.2 * atr
                target = t_struct if (t_struct - c) >= self.p.k_C * risk else c + self.p.k_C * risk
                return (1, c, stop, target, self.p.k_C)
        # 摸顶做空
        if self.data.high[-1] >= hi - tol and c < hi:
            if self._reversal_bear(hi, atr) or rsi > 70:
                stop = hi + self.p.atr_stop_mult_C * atr
                risk = max(abs(stop - c), 0.4 * atr)
                stop = c + risk
                mid = (hi + lo) / 2.0
                t_struct = max(lo + 0.2 * atr, mid) if mid < c else lo + 0.2 * atr
                target = t_struct if (c - t_struct) >= self.p.k_C * risk else c - self.p.k_C * risk
                return (-1, c, stop, target, self.p.k_C)
        self._rej("C_no_touch")
        return None

    # ------------------------------------------------------------------ 主循环
    def next(self):
        dt = self._now_dt()
        # 每根 bar 都调用: 让 _last_sd 连续跟踪日期, 否则只在持仓时更新会在
        # "新开仓与上一笔不同日"时误判为隔夜 → 凭空产生 swap
        self._charge_swap()
        self.equity.append((dt, self.broker.getvalue()))
        self._roll_day(dt)
        hr = self._server_hour(dt)
        self._update_day_ohlc()
        self._update_or(hr)

        # 1) 平仓检测(broker 端挂单已成交)
        for o, reason in ((self.sl_order, "sl"), (self.tp_order, "tp"),
                          (self.manual_order, "eod")):
            if o is not None and getattr(o, "status", None) == bt.Order.Completed:
                self._log_close(reason, o)
                self._finalize_close(reason)
                return

        # 2) 入场单已成交 → 挂 SL/TP
        if self.position and self.sl_order is None and self.tp_order is None and self.trade_info:
            self._attach_sl_tp()
            self._hold_bars = 0

        # 3) 持仓管理
        if self.position:
            if self._margin_call():
                self._log_close("margin", None)
                self._finalize_close("margin")
                return
            if self.p.trailing:
                self._maybe_trail()
            self._hold_bars += 1
            # 时间止损
            if self.p.max_hold_bars and self._hold_bars >= self.p.max_hold_bars:
                self._manual_close()
                return
            # EOD
            if self.p.avoid_overnight:
                if hr >= (self.p.server_close_hour - self.p.eod_lead_hours):
                    self._manual_close()
                    return
            return

        # 4) 无持仓 → 评估信号
        if self._no_trade_before and dt.date() < self._no_trade_before:
            return
        atr = self._atr_now()
        if not atr or atr <= 0:
            return
        c = self.data.close[-1]
        regime = self._compute_regime()

        if regime == "VOL_SHOCK":
            self._shock_until = len(self) + self.p.shock_freeze_bars
            self._rej("vol_shock")
            return

        if not self._pass_filters(hr, atr):
            return

        allowed = []
        if regime in ("TREND_UP", "TREND_DOWN"):
            if self.p.enable_A:
                allowed.append("A")
            if self.p.enable_B:
                allowed.append("B")
        elif regime == "RANGE":
            if self.p.enable_C:
                allowed.append("C")
            if self.p.enable_B:
                allowed.append("B")
        else:  # TRANSITION
            if self.p.allow_in_transition and self.p.enable_A:
                allowed.append("A")
            if self.p.allow_B_in_transition and self.p.enable_B:
                allowed.append("B")

        sig = None
        for fam in allowed:
            if fam == "A":
                sig = self._signal_A(regime, atr, c)
            elif fam == "B":
                sig = self._signal_B(regime, atr, c)
            else:
                sig = self._signal_C(regime, atr, c)
            if sig:
                break

        if not sig:
            return
        direction, entry_ref, stop, target, rm = sig
        # 方向必须与 regime 一致
        if regime == "TREND_UP" and direction != 1:
            self._rej("dir_conflict")
            return
        if regime == "TREND_DOWN" and direction != -1:
            self._rej("dir_conflict")
            return
        # 成本闸门
        risk_usd = abs(entry_ref - stop)
        tgt_usd = abs(target - entry_ref)
        if risk_usd < self.p.min_risk_usd or tgt_usd < self.p.min_target_usd:
            self._rej("cost_gate")
            return
        if tgt_usd / risk_usd < 1.0:
            self._rej("rr_gate")
            return

        self.n_entries += 1
        self._trades_today += 1
        self._open_position("long" if direction == 1 else "short",
                            entry_ref, stop, target, rm, self.rsi[-1])

    def _finalize_close(self, reason):
        """平仓收尾: 记 R、更新冷却与日内盈亏, 再复位。"""
        r = None
        try:
            info = self.trade_info or {}
            entry, stop = info.get("entry"), info.get("stop")
            px = self.trades_log[-1]["price"] if self.trades_log else None
            if entry and stop and px:
                risk = abs(entry - stop)
                if risk > 0:
                    r = ((px - entry) if info.get("dir") == 1 else (entry - px)) / risk
                    self._pnl_r_today += r
        except Exception:
            pass
        # 冷却: 亏损后加长, 盈利后缩短
        self._pending_cooldown = (int(self.p.cooldown_after_loss)
                                  if (r is not None and r < 0)
                                  else int(self.p.cooldown_after_win))
        self._last_entry_bar = len(self.data)
        self._last_entry_side = (self.trade_info or {}).get("dir")
        self._reset_pos()
        self._hold_bars = 0


# ============================ 回测运行器 ============================

def run_m5_backtest(extra_args=None, cash=1000.0, leverage=1000.0, lot=0.01,
                    spread=0.33, commission=0.0,
                    fromdate=None, todate=None, quiet=True,
                    primary_freq="5m", context_freqs=("15m", "1h")):
    """M5 主周期回测。返回扩展指标(含频率类 KPI)。"""
    cerebro = bt.Cerebro()
    cerebro.addsizer(FixedLotSizer, lot=lot)

    df_primary, data_primary, sp_primary = load_data(primary_freq, fromdate, todate)
    cerebro.adddata(data_primary, name=primary_freq)
    for cf in context_freqs:
        _, data_cf, _ = load_data(cf, fromdate, todate)
        cerebro.adddata(data_cf, name=cf)

    cerebro.addstrategy(M5MultiSignalStrategy, spread_series=sp_primary,
                        **(extra_args or {}))

    comminfo = bt.CommissionInfo(commission=commission, mult=100.0,
                                 leverage=leverage, stocklike=False)
    cerebro.broker.addcommissioninfo(comminfo)
    cerebro.broker.setcash(cash)
    cerebro.broker.set_slippage_fixed(fixed=spread / 2.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")

    results = cerebro.run(optreturn=False)
    strat = results[0]
    dd = strat.analyzers.dd.get_analysis()

    final = cerebro.broker.getvalue()
    total_ret = final / cash - 1.0
    days = (df_primary.index[-1] - df_primary.index[0]).days
    years = max(days, 1) / 365.0
    ann = (1.0 + total_ret) ** (1.0 / years) - 1.0 if total_ret > -1 else -1.0

    # ---- FIFO 配对(复用既有口径) ----
    tl = strat.trades_log
    stack, paired = [], []
    for t in tl:
        if t["kind"] == "open":
            stack.append(t)
        elif t["kind"] == "close" and stack:
            paired.append((stack.pop(), t))
    gp = gl = 0.0
    won = 0
    rsum = 0.0
    longs = shorts = 0
    gp_l = gl_l = gp_s = gl_s = 0.0
    for o, c in paired:
        sign = 1 if o["side"] == "long" else -1
        pnl = (c["price"] - o["price"]) * sign
        risk = abs(o["price"] - (o["stop"] or o["price"]))
        if risk > 0:
            rsum += pnl / risk
        if pnl >= 0:
            gp += pnl
            won += 1
            if sign == 1:
                gp_l += pnl
            else:
                gp_s += pnl
        else:
            gl += -pnl
            if sign == 1:
                gl_l += -pnl
            else:
                gl_s += -pnl
        if sign == 1:
            longs += 1
        else:
            shorts += 1

    closed = len(paired)
    # ---- 频率类 KPI ----
    trade_days = Counter()
    for o, c in paired:
        trade_days[c["dt"].date()] += 1
    all_days = pd.Index(df_primary.index).normalize().unique()
    n_days = max(len(all_days), 1)
    trades_per_day = closed / n_days if n_days else 0.0
    pct_days_traded = len(trade_days) / n_days if n_days else 0.0
    # 最长无交易天数
    longest_gap = 0
    if len(all_days) > 1:
        ds = sorted(set(d.date() for d in all_days))
        prev = None
        gap = 0
        for d in ds:
            if d in trade_days:
                gap = 0
            else:
                gap += 1
                longest_gap = max(longest_gap, gap)
    hour_hist = Counter()
    reason_hist = Counter()
    for o, c in paired:
        hour_hist[c["dt"].hour] += 1
        reason_hist[c.get("reason")] += 1

    res = dict(
        final=final, total_ret=total_ret, ann=ann,
        maxdd=dd.max.drawdown if hasattr(dd, "max") else 0.0,
        closed=closed, won=won, lost=closed - won,
        win_rate=(won / closed) if closed else 0.0,
        gross_profit=gp, gross_loss=gl,
        pf=(gp / gl) if gl > 0 else float("inf"),
        realized_ret=(gp - gl) / cash,
        avg_R=(rsum / closed) if closed else 0.0,
        expectancy=(gp - gl) / closed if closed else 0.0,
        trades_per_day=trades_per_day,
        pct_days_traded=pct_days_traded,
        longest_no_trade_days=longest_gap,
        n_long=longs, n_short=shorts,
        pf_long=(gp_l / gl_l) if gl_l > 0 else float("inf"),
        pf_short=(gp_s / gl_s) if gl_s > 0 else float("inf"),
        n_days=int(n_days),
        entries=strat.n_entries,
        swap_total=getattr(strat, "swap_total", 0.0),
        eq=getattr(strat, "equity", []),
        trades=tl,
        hour_hist=dict(hour_hist),
        reason_hist=dict(reason_hist),
        rejects=dict(getattr(strat, "rejects", {})),
    )
    if not quiet:
        print(f"[M5] ret={total_ret*100:+.2f}% ann={ann*100:+.1f}% PF={res['pf']:.2f} "
              f"WR={res['win_rate']*100:.1f}% closed={closed} "
              f"trades/day={trades_per_day:.2f} days_traded={pct_days_traded*100:.0f}% "
              f"DD={res['maxdd']:.1f}% final=${final:.2f}")
        print(f"     long={longs}(PF {res['pf_long']:.2f}) short={shorts}(PF {res['pf_short']:.2f}) "
              f"avg_R={res['avg_R']:.2f} exp=${res['expectancy']:.2f}")
        if res["rejects"]:
            top = sorted(res["rejects"].items(), key=lambda kv: -kv[1])[:6]
            print("     拒绝TOP: " + ", ".join(f"{k}={v}" for k, v in top))
    return res


M5_PROFILES = {
    "default": dict(),
    "freq_first": dict(enable_A=True, enable_B=True, enable_C=True,
                       max_trades_day=99, cooldown_bars=3),
    "quality_first": dict(enable_A=True, enable_B=False, enable_C=True,
                          max_trades_day=6, cooldown_bars=12),
    # ---- v1 实测选出的两个操作点(2025-04-15~2026-09-11 全样本, 未做样本外验证!) ----
    # 高质量端: 关掉无优势的 A, 解锁 C, 允许 B 在 TRANSITION 开火
    "best_v1": dict(enable_A=False, range_rel_mult=1.5, allow_B_in_transition=True,
                    stop_min_atr=1.5, stop_max_atr=2.5,
                    k_A=1.2, k_C=1.2, rg_span_max_atr=6.0, pb_tol_atr=0.6),
    # 高频端: 在 best_v1 基础上把 B 的唐奇安缩短到 60 分钟
    "freq_v1": dict(enable_A=False, range_rel_mult=1.5, allow_B_in_transition=True,
                    dc_minutes=60, bo_buf_atr=0.05, vol_mult=1.0,
                    stop_min_atr=1.5, stop_max_atr=2.5,
                    k_C=1.2, rg_span_max_atr=6.0),
}


def _main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2025-04-15")
    ap.add_argument("--to", dest="to", default="2026-09-11")
    ap.add_argument("--cash", type=float, default=1000.0)
    ap.add_argument("--spread", type=float, default=0.33)
    ap.add_argument("--profile", default="default")
    ap.add_argument("--print-rejects", action="store_true")
    a = ap.parse_args()
    extra = dict(M5_PROFILES.get(a.profile, {}))
    if a.print_rejects:
        extra["print_rejects"] = True
    run_m5_backtest(extra_args=extra, cash=a.cash, spread=a.spread,
                    fromdate=a.frm, todate=a.to, quiet=False)


if __name__ == "__main__":
    _main()
