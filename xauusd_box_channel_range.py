# -*- coding: utf-8 -*-
"""
XAU/USD 短线箱体通道 + 支撑/压力反转策略 (backtrader)
========================================================

数据架构
--------
主周期 : 15m  (self.data)   —— 逐根 bar 做入场/止损/止盈判定 (对应"每分钟回测")
辅助周期: 1h   (self.data1)  —— 判定上升/下降/震荡通道, 以及长期支撑压力位

需求 -> 实现 映射
-----------------
[1] 多空逻辑
    - 用"该时间点之前的小时数据" (self.data1, 1h) 判定通道:
        上升通道 -> 只允许做多
        下降通道 -> 只允许做空
        震荡     -> 不交易
    - 通道方向用 收盘价线性回归斜率(相对) + 价格相对 Donchian 中轨位置 联合判定。

[2] 支撑位 / 压力位
    (a) 短期 (15m, 最近 short_win=8 根 = 2h):
        对窗口内每根 K 线的 (high+low)/2 做直方图聚类, 找最密集价格带;
        若落入该带的 K 线数 >= min_k(3), 取这些 K 线的 min(low) 为支撑 / max(high) 为压力;
        否则退化为该窗口的极值 (震荡区间极值)。
    (b) 长期 (1h, 最近 long_win=4 根 = 4h, 覆盖"2~4 小时"):
        同样聚类逻辑, 得到长期支撑/压力。
    (c) 趋势反转标记:
        当前价接近"强压力/支撑位"(被 touch_count>=2 次测试) 且出现反转 K 线
        (压力位附近收阴 / 支撑位附近收阳) -> 标记短期/长期反转。

[3] 买卖规则
    - 做多: 上升通道 且 当根下探支撑位但收于支撑之上 (未跌破) -> 入场
    - 做空: 下降通道 且 当根上探压力位但收于压力之下 (未突破) -> 入场
    - 止损: 做多 = 支撑位 K 线最低 low - sl_buffer; 做空 = 压力位 K 线最高 high + sl_buffer
    - 止盈: 做多 = entry + r_mult*(entry - stop)
            做空 = entry - r_mult*(stop - entry)   (r_mult 默认 1.2)

用法
----
    python xauusd_box_channel_strategy.py --freq 15m --plot
    python xauusd_box_channel_strategy.py --r-mult 1.5 --log
"""
import os
import argparse
import datetime as dt
import numpy as np
import pandas as pd
import backtrader as bt

CLEAN_DIR = r"D:\Data\stockdata\xauusd\clean"
FIG_DIR = os.path.join(CLEAN_DIR, "figs")


# --------------------------------------------------------------------------
# TMGM 隔夜费 (swap) 建模辅助
# --------------------------------------------------------------------------
def tmgm_server_offset_hours(utc_dt):
    """TMGM 服务器相对 UTC 的偏移: 跟随美国 DST, 夏令 +3 / 冬令 +2。

    swap 在服务器零点(UTC 21:00 夏 / 22:00 冬)收取。"""
    year = utc_dt.year
    mar1 = dt.datetime(year, 3, 1)
    first_sun_mar = mar1 + dt.timedelta(days=(6 - mar1.weekday()) % 7)
    dst_start = (first_sun_mar + dt.timedelta(days=7)).replace(hour=7)  # 02:00 EST=07:00 UTC
    nov1 = dt.datetime(year, 11, 1)
    first_sun_nov = nov1 + dt.timedelta(days=(6 - nov1.weekday()) % 7)
    dst_end = first_sun_nov.replace(hour=6)  # 02:00 EDT=06:00 UTC
    return 3 if (dst_start <= utc_dt < dst_end) else 2


# --------------------------------------------------------------------------
# 固定手数 sizer (TMGM: 每次固定 lot 手 = lot×100 盎司)
# --------------------------------------------------------------------------
class FixedLotSizer(bt.Sizer):
    params = dict(lot=0.01)

    def _getsizing(self, comminfo, cash, data, isbuy):
        return self.p.lot


# --------------------------------------------------------------------------
# 策略主体
# --------------------------------------------------------------------------
class BoxChannelStrategy(bt.Strategy):
    params = dict(
        trend_window=24,        # 1h 根数 -> 通道判定窗口 (约 24h)
        short_win=8,            # 15m 根数 -> 2h 内短期支撑压力
        long_win=4,             # 1h 根数 -> 4h 内长期支撑压力
        min_k=3,                # 密集带至少需落入的 K 线数
        cluster_bins=20,        # 聚类直方图分箱数
        r_mult=1.2,             # 止盈 / 止损 风险倍数
        sl_buffer=2.0,          # 止损相对触发极值的美元外推
        rel_slope_thresh=0.0006,  # 通道方向判定: 斜率相对阈值
        rev_tol=0.0015,         # 价格"接近"支撑/压力的容差(相对)
        touch_count=2,          # 视作"强"支撑/压力需被触及次数
        use_reversal_filter=True,   # 入场需反转 K 线确认 (默认开, 提升入场质量)
        short_trend_n=20,           # 15m 短期方向判定窗口 (根)
        cooldown_bars=4,            # 同方向入场冷却根数 (15m), 防同支撑/压力位反复刷
        rsi_period=14,              # RSI 周期
        rsi_overbought=70,          # RSI 超买阈值 (做多过滤)
        rsi_oversold=30,            # RSI 超卖阈值 (做空过滤)
        min_wick_ratio=0.6,         # 反转 K 线: 影线占整根比例阈值 (>= 视为锤子/上吊)
        print_log=False,
        # ---- 震荡(range)分支: 基线在此空仓, 本模块新增可选交易逻辑 ----
        range_mode="off",           # off|ma_cross|pullback|ma_follow|range_break
        rng_ma_fast=5,              # 快均线周期
        rng_ma_slow=20,             # 慢均线周期
        rng_ma_period=20,           # pullback 参考均线周期
        rng_lookback=20,            # range_break 唐奇安窗口(根, 不含当根)
        rng_atr_period=14,          # ATR 周期(止损距离)
        rng_atr_mult=1.5,           # 止损 = rng_atr_mult * ATR
        rng_r_mult=None,            # 止盈风险倍数(None=沿用主 r_mult)
        rng_pullback_tol=0.0015,    # 回踩容差(相对)
        rng_use_sr=True,            # 结合短期支撑压力过滤
        swap_long_points=-72.5,    # 多单隔夜费 (点), 实测
        swap_short_points=30.72,   # 空单隔夜费 (点), 实测 (正=返息)
        swap_point_value=0.01,     # 1 点 = 0.01 USD/盎司
        contract_oz=100.0,         # 1 手 = 100 盎司
    )

    def __init__(self):
        self.order = None
        self.trade_info = {}        # 当前持仓的 entry/stop/target/dir
        self.equity = []            # 资金曲线记录 (datetime, value)
        self.n_entries = 0
        self.trades_log = []        # 开/平仓点 (dt, price, side, kind, reason)
        self._close_reason = None   # 平仓原因: tp=止盈 / sl=止损 / margin=强平 (开仓置空)
        self._prev_pos = 0
        self._last_sd = None        # 上次 TMGM 服务器日 (隔夜费计费用)
        self.swap_total = 0.0       # 累计隔夜费 (负=扣, 正=返)
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        # 震荡分支指标
        self.ma_fast = bt.indicators.SMA(self.data.close, period=int(self.p.rng_ma_fast))
        self.ma_slow = bt.indicators.SMA(self.data.close, period=int(self.p.rng_ma_slow))
        self.ma_pb = bt.indicators.SMA(self.data.close, period=int(self.p.rng_ma_period))
        self.atr = bt.indicators.ATR(self.data, period=int(self.p.rng_atr_period))
        self._open_tag = None       # 开仓来源: box=主线 / 模式名=震荡分支
        self._last_entry_bar = -999 # 入场冷却: 上次开仓的 15m bar 序号
        self._last_entry_side = None

    # ---------------- 通道判定 (1h) ----------------
    def classify_channel(self):
        n = self.p.trend_window
        if len(self.data1) < n + 1:
            return "warmup"
        closes = np.array(self.data1.close.get(size=n), dtype=float)
        highs = np.array(self.data1.high.get(size=n), dtype=float)
        lows = np.array(self.data1.low.get(size=n), dtype=float)
        x = np.arange(n)
        slope = np.polyfit(x, closes, 1)[0]          # 收盘价线性回归斜率
        mid = float(np.mean(closes))
        rel_slope = slope / mid if mid > 0 else 0.0  # 相对斜率
        cur = self.data1.close[0]
        # 上轨/下轨取窗口极值, 中轨取均值 -> Donchian 箱体
        if rel_slope > self.p.rel_slope_thresh and cur >= mid:
            return "up"
        if rel_slope < -self.p.rel_slope_thresh and cur <= mid:
            return "down"
        return "range"

    # ---------------- 15m 短期方向判定 ----------------
    def classify_short_direction(self):
        """基于 15m 近 short_trend_n 根收盘斜率判定短期方向。
        返回 1=向上, -1=向下, 0=横盘。用于入场质量过滤:
        大周期向上时若 15m 短期明显向下则不抄底(避免逆势接刀)。"""
        n = self.p.short_trend_n
        if len(self.data) < n + 1:
            return 0
        closes = np.array(self.data.close.get(size=n), dtype=float)
        x = np.arange(n)
        slope = np.polyfit(x, closes, 1)[0]
        mid = float(np.mean(closes))
        rel = slope / mid if mid > 0 else 0.0
        th = self.p.rel_slope_thresh * 0.5   # 15m 窗口短, 阈值略低于 1h
        if rel > th:
            return 1
        if rel < -th:
            return -1
        return 0

    # ---------------- 聚类找支撑/压力 ----------------
    def cluster_levels(self, data, n):
        """返回 (support, resistance, touch_high, touch_low)
        touch_high/low: 压力/支撑位被测试(触及)的次数"""
        if len(data) < n + 1:
            return None, None, 0, 0
        highs = np.array(data.high.get(size=n), dtype=float)
        lows = np.array(data.low.get(size=n), dtype=float)
        mids = (highs + lows) / 2.0
        lo, hi = float(lows.min()), float(highs.max())
        if hi <= lo:
            return lo, hi, 0, 0
        counts, edges = np.histogram(mids, bins=self.p.cluster_bins, range=(lo, hi))
        best = int(np.argmax(counts))
        b_lo, b_hi = edges[best], edges[best + 1]
        mask = (mids >= b_lo) & (mids <= b_hi)
        if mask.sum() >= self.p.min_k:
            support = float(lows[mask].min())
            resistance = float(highs[mask].max())
        else:
            # 退化为该窗口极值 (即震荡区间极值)
            support = lo
            resistance = hi
        rtol = self.p.rev_tol
        touch_high = int(np.sum(highs >= resistance * (1 - rtol)))
        touch_low = int(np.sum(lows <= support * (1 + rtol)))
        return support, resistance, touch_high, touch_low

    # ---------------- 反转 K 线确认 ----------------
    def detect_reversal(self, support, resistance):
        """检测当根 15m 是否出现真实反转 K 线 (锤子/上吊 + 吞没),
        且发生在支撑/压力附近。取代原"收阳+接近"弱确认。
        返回 {long:做多反转信号, short:做空反转信号}。"""
        rev = {"short": False, "long": False}
        if support is None and resistance is None:
            return rev
        o, h, l, c = self.data.open[0], self.data.high[0], self.data.low[0], self.data.close[0]
        rng = h - l
        if rng <= 0:
            return rev
        upper = h - max(o, c)      # 上影线
        lower = min(o, c) - l      # 下影线
        # 锤子线 (做多): 下影占整根 >= min_wick_ratio 且收阳 -> 下探回升
        hammer = (lower / rng >= self.p.min_wick_ratio) and (c >= o)
        # 上吊线 (做空): 上影占整根 >= min_wick_ratio 且收阴 -> 上探回落
        hanging = (upper / rng >= self.p.min_wick_ratio) and (c <= o)
        # 吞没 (需前一根): 前阴后阳=看涨吞没, 前阳后阴=看跌吞没
        bull_engulf = bear_engulf = False
        if len(self.data) >= 2:
            po, pc = self.data.open[-1], self.data.close[-1]
            if pc < po and c > o and c > po:
                bull_engulf = True
            if pc > po and c < o and c < po:
                bear_engulf = True
        near_sup = support is not None and abs(c - support) / support < self.p.rev_tol * 3
        near_res = resistance is not None and abs(c - resistance) / resistance < self.p.rev_tol * 3
        if (hammer or bull_engulf) and near_sup:
            rev["long"] = True
        if (hanging or bear_engulf) and near_res:
            rev["short"] = True
        return rev

    # ---------------- 订单状态回调 ----------------
    def notify_order(self, order):
        if order.status in (order.Completed, order.Canceled, order.Margin, order.Rejected):
            self.order = None
        if order.status == order.Completed:
            prev = self._prev_pos
            cur = self.position.size
            if order.isbuy():
                side, kind = ("long", "open") if prev == 0 else ("short", "close")
            else:
                side, kind = ("short", "open") if prev == 0 else ("long", "close")
            self._prev_pos = cur
            self.trades_log.append(dict(
                dt=bt.num2date(order.executed.dt),
                price=order.executed.price,
                side=side,
                kind=kind,
                reason=getattr(self, "_close_reason", None),
                tag=self._open_tag,
            ))
            if kind == "close":
                self._close_reason = None   # 平仓记录后重置, 避免污染下一笔

    def _margin_call(self, maintenance=0.5):
        """TMGM 维持保证金强平: 净值 < 已用保证金*maintenance 时平仓。"""
        if not self.position:
            return False
        ci = self.broker.getcommissioninfo(self.data)
        mult = getattr(ci.p, "mult", 1.0) or 1.0
        lev = getattr(ci.p, "leverage", 1.0) or 1.0
        used = abs(self.position.size) * self.data.close[0] * mult / lev
        if self.broker.get_value() < used * maintenance:
            self._close_reason = "margin"
            self.order = self.close()
            return True
        return False

    def _charge_swap(self):
        """跨过 TMGM 服务器零点(UTC 21:00 夏 / 22:00 冬)时对当前持仓收隔夜费。
        多单 -72.5 点/晚, 空单 +30.72 点/晚 (点值 0.01 USD/盎司), 周三3倍。"""
        utc_dt = bt.num2date(self.data.datetime[0])   # backtrader 内部日期格式
        off = tmgm_server_offset_hours(utc_dt)
        sd = (utc_dt + dt.timedelta(hours=off)).date()
        if self._last_sd is None:
            self._last_sd = sd
            return
        if sd == self._last_sd:
            return
        mult3 = 3 if sd.weekday() == 2 else 1   # 周三过夜收 3 倍
        pos = self.position
        if pos:
            oz = abs(pos.size) * self.p.contract_oz
            pts = self.p.swap_long_points if pos.size > 0 else self.p.swap_short_points
            usd = oz * pts * self.p.swap_point_value * mult3
            self.broker.add_cash(usd)   # 多单为负(扣费), 空单为正(返息)
            self.swap_total += usd
        self._last_sd = sd

    def log(self, txt):
        if self.p.print_log:
            dt = self.data.datetime.datetime(0)
            print(f"{dt} {txt}")

    # ---------------- 主循环 ----------------
    def next(self):
        self.equity.append((self.data.datetime.datetime(0), self.broker.getvalue()))
        self._charge_swap()
        if self.order:
            return
        # TMGM 保证金强平: 净值低于维持保证金则平仓
        if self.position and self._margin_call():
            return

        # 持仓中: 任何通道都先检查止损/止盈
        if self.position:
            c = self.data.close[0]
            info = self.trade_info
            is_long = info.get("dir") == 1
            # 区分止盈(tp)/止损(sl): 多头下破支撑=止损, 上破目标=止盈; 空头反之
            if is_long:
                if c <= info["stop"]:
                    self._close_reason = "sl"
                elif c >= info["target"]:
                    self._close_reason = "tp"
                else:
                    self._close_reason = None
            else:
                if c >= info["stop"]:
                    self._close_reason = "sl"
                elif c <= info["target"]:
                    self._close_reason = "tp"
                else:
                    self._close_reason = None
            if self._close_reason:
                self.order = self.close()
                self.log(f"CLOSE {'long' if is_long else 'short'} @ {c:.2f} "
                         f"reason={self._close_reason} (stop={info['stop']:.2f} target={info['target']:.2f})")
            return

        # 开仓需先判定通道方向
        channel = self.classify_channel()
        if channel == "warmup":
            return  # 预热 -> 不交易
        if channel == "range":
            # 震荡: 基线在此空仓; range_mode != off 时改走新增的震荡分支
            if self.p.range_mode != "off":
                self._range_entry()
            return

        # 入场质量过滤 1: 15m 短期方向不能明显逆大周期 (避免逆势接刀)
        short_dir = self.classify_short_direction()
        if channel == "up" and short_dir < 0:
            return   # 1h 向上但 15m 加速向下 -> 不抄底
        if channel == "down" and short_dir > 0:
            return   # 1h 向下但 15m 加速向上 -> 不摸顶

        # 短期 (15m) 支撑压力
        s_sup, s_res, s_th, s_tl = self.cluster_levels(self.data, self.p.short_win)
        # 长期 (1h) 支撑压力
        l_sup, l_res, l_th, l_tl = self.cluster_levels(self.data1, self.p.long_win)
        if s_sup is None or l_res is None:
            return

        # 优先用短期支撑/压力, 退化用长期
        support = s_sup
        resistance = s_res

        # 入场质量过滤 2: 反转 K 线确认 (必须出现真实反转形态且接近支撑/压力)
        rev = self.detect_reversal(support, resistance)
        need_rev = self.p.use_reversal_filter
        if need_rev and not (rev["long"] or rev["short"]):
            return

        # 入场质量过滤 3: RSI 极端过滤 (超买不做多 / 超卖不做空)
        rsi = self.rsi[0]
        if rsi == rsi:   # 跳过 RSI warmup 期 NaN
            if channel == "up" and rsi > self.p.rsi_overbought:
                return
            if channel == "down" and rsi < self.p.rsi_oversold:
                return

        o, h, l, c = self.data.open[0], self.data.high[0], self.data.low[0], self.data.close[0]

        # 期望开仓方向 (尊重 use_reversal_filter 开关)
        if channel == "up":
            want_side = "long" if (not need_rev or rev["long"]) else None
        elif channel == "down":
            want_side = "short" if (not need_rev or rev["short"]) else None
        else:
            want_side = None
        if want_side is None:
            return
        # 入场质量过滤 4: 同方向冷却 (防同一支撑/压力位短时间反复刷)
        bar = len(self.data)
        if self._last_entry_side == want_side and (bar - self._last_entry_bar) < self.p.cooldown_bars:
            return

        # ---- 做多: 上升通道 + 反转 K 线 + 价格触及支撑未跌破 ----
        if want_side == "long":
            if l <= support <= c:   # 当根下探支撑、收于其上 (锤子形态)
                stop = support - self.p.sl_buffer
                target = c + self.p.r_mult * (c - stop)
                self._open_tag = "box"
                self.order = self.buy()
                self.trade_info = dict(entry=c, stop=stop, target=target, dir=1)
                self._last_entry_bar = bar
                self._last_entry_side = "long"
                self.n_entries += 1
                self.log(f"BUY  @ {c:.2f} rsi={rsi:.1f} stop {stop:.2f} target {target:.2f}")

        # ---- 做空: 下降通道 + 反转 K 线 + 价格触及压力未突破 ----
        elif want_side == "short":
            if c <= resistance <= h:   # 当根上探压力、收于其下 (上吊形态)
                stop = resistance + self.p.sl_buffer
                target = c - self.p.r_mult * (stop - c)
                self._open_tag = "box"
                self.order = self.sell()
                self.trade_info = dict(entry=c, stop=stop, target=target, dir=-1)
                self._last_entry_bar = bar
                self._last_entry_side = "short"
                self.n_entries += 1
                self.log(f"SELL @ {c:.2f} rsi={rsi:.1f} stop {stop:.2f} target {target:.2f}")

    # ==================== 震荡分支 (range_mode) ====================
    def _range_ready(self):
        """震荡分支通用前检: 指标 warmup。"""
        if self.p.range_mode == "off":
            return False
        n = max(int(self.p.rng_ma_slow), int(self.p.rng_ma_period),
                int(self.p.rng_lookback)) + 2
        return len(self.data) >= n

    def _atr_val(self):
        try:
            v = float(self.atr[0])
        except Exception:
            return None
        if v != v or v <= 0:      # NaN 或 0
            return None
        return v

    def _open_bracket(self, side, c, stop, target, tag):
        """统一下单 + 记录 trade_info(供公共的止损/止盈逻辑复用)。"""
        self._open_tag = tag
        self.order = self.buy() if side == "long" else self.sell()
        self.trade_info = dict(entry=c, stop=stop, target=target,
                               dir=(1 if side == "long" else -1))
        self._last_entry_bar = len(self.data)
        self._last_entry_side = side
        self.n_entries += 1
        self.log(f"R-{tag} {side.upper()} @ {c:.2f} stop {stop:.2f} target {target:.2f}")

    # ---- 模式1: MA 金叉 / 死叉 ----
    def _sig_ma_cross(self):
        f0, f1 = self.ma_fast[0], self.ma_fast[-1]
        s0, s1 = self.ma_slow[0], self.ma_slow[-1]
        if None in (f0, f1, s0, s1):
            return None
        if f1 <= s1 and f0 > s0:
            return "long"      # 金叉
        if f1 >= s1 and f0 < s0:
            return "short"     # 死叉
        return None

    # ---- 模式2: 回踩均线不破 ----
    def _sig_pullback(self, h, l, c):
        f0, s0, p0 = self.ma_fast[0], self.ma_slow[0], self.ma_pb[0]
        if None in (f0, s0, p0):
            return None
        tol = float(self.p.rng_pullback_tol)
        if f0 > s0 and l <= p0 * (1 + tol) and c > p0:
            return "long"      # 多头背景下探均线、收盘守住
        if f0 < s0 and h >= p0 * (1 - tol) and c < p0:
            return "short"     # 空头背景上探均线、收盘压制
        return None

    # ---- 模式3: 短期均线趋势跟随(不追高) ----
    def _sig_ma_follow(self, c):
        f0, f1 = self.ma_fast[0], self.ma_fast[-1]
        s0 = self.ma_slow[0]
        c1 = self.data.close[-1]
        if None in (f0, f1, s0, c1):
            return None
        tol = float(self.p.rng_pullback_tol)
        if f0 > s0 and c > f0 and c1 <= f1 and (c - f0) / f0 < tol * 3:
            return "long"      # 多头排列 + 价格上穿快线且未远离
        if f0 < s0 and c < f0 and c1 >= f1 and (f0 - c) / f0 < tol * 3:
            return "short"
        return None

    # ---- 模式4(自创): 震荡区间突破(唐奇安, 不含当根) ----
    def _sig_range_break(self, c):
        nb = int(self.p.rng_lookback)
        arr_h = list(self.data.high.get(size=nb + 1))
        arr_l = list(self.data.low.get(size=nb + 1))
        arr_h, arr_l = arr_h[:-1], arr_l[:-1]   # 剔除信号 bar 自身
        if len(arr_h) < nb:
            return None
        hh = max(float(x) for x in arr_h)
        ll = min(float(x) for x in arr_l)
        if c > hh:
            return "long"
        if c < ll:
            return "short"
        return None

    def _range_entry(self):
        """震荡市(range)下的独立交易逻辑入口。"""
        if not self._range_ready():
            return
        o, h, l, c = (self.data.open[0], self.data.high[0],
                      self.data.low[0], self.data.close[0])
        atr = self._atr_val()
        if atr is None:
            return
        mode = self.p.range_mode
        want = None
        if mode == "ma_cross":
            want = self._sig_ma_cross()
        elif mode == "pullback":
            want = self._sig_pullback(h, l, c)
        elif mode == "ma_follow":
            want = self._sig_ma_follow(c)
        elif mode == "range_break":
            want = self._sig_range_break(c)
        if want is None:
            return

        # RSI 极端过滤(与主线一致: 超买不做多 / 超卖不做空)
        rsi = self.rsi[0]
        if rsi == rsi:
            if want == "long" and rsi > self.p.rsi_overbought:
                return
            if want == "short" and rsi < self.p.rsi_oversold:
                return

        # 同方向冷却
        bar = len(self.data)
        if self._last_entry_side == want and (bar - self._last_entry_bar) < self.p.cooldown_bars:
            return

        # 结合短期支撑压力: 做多不贴压力(无空间), 做空不贴支撑
        if self.p.rng_use_sr:
            try:
                sup, res, _, _ = self.cluster_levels(self.data, self.p.short_win)
            except Exception:
                sup = res = None
            if sup is not None and res is not None:
                tol = float(self.p.rng_pullback_tol)
                if want == "long" and c >= res * (1 - tol):
                    return
                if want == "short" and c <= sup * (1 + tol):
                    return

        # 止损/止盈: ATR 定风险, r_mult 定盈亏比
        rm = float(self.p.r_mult if self.p.rng_r_mult is None else self.p.rng_r_mult)
        risk = float(self.p.rng_atr_mult) * atr
        if want == "long":
            stop, target = c - risk, c + rm * risk
        else:
            stop, target = c + risk, c - rm * risk
        self._open_bracket(want, c, stop, target, mode)


# --------------------------------------------------------------------------
# 数据加载
# --------------------------------------------------------------------------
def load_data(freq):
    path = os.path.join(CLEAN_DIR, f"xauusd_{freq}_utc.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df, bt.feeds.PandasData(dataname=df)


# --------------------------------------------------------------------------
# 运行回测
# --------------------------------------------------------------------------
def run_backtest(freq="15m", plot=False, extra_args=None,
                 cash=1000.0, leverage=1000.0, lot=0.01,
                 spread=0.33, commission=0.0):
    cerebro = bt.Cerebro()
    cerebro.addsizer(FixedLotSizer, lot=lot)
    cerebro.addstrategy(BoxChannelStrategy, **(extra_args or {}))

    df15, data15 = load_data("15m")
    cerebro.adddata(data15, name="15m")
    df1h, data1h = load_data("1h")
    cerebro.adddata(data1h, name="1h")

    # TMGM 真实交易方式: 杠杆 1:leverage, 固定 lot 手, 点差 spread USD/oz
    comminfo = bt.CommissionInfo(commission=commission, mult=100.0,
                                 leverage=leverage, stocklike=False)
    cerebro.broker.addcommissioninfo(comminfo)
    cerebro.broker.setcash(cash)
    # 点差只在【开仓】收一次 (TMGM 规则): set_slippage_fixed 会开+平各收,
    # 故用 spread/2 使 round-trip 成本 = 1x spread, 与"仅开仓收"等价。
    cerebro.broker.set_slippage_fixed(fixed=spread / 2.0)

    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="ta")
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")

    print(f"[run] 主周期=15m 辅助=1h 初始资金={cash}USD 杠杆=1:{leverage} 手数={lot} "
          f"点差={spread}USD/oz(仅开仓收) 佣金={commission} "
          f"隔夜费=多{(extra_args or {}).get('swap_long_points', -72.5)}/"
          f"空{(extra_args or {}).get('swap_short_points', 30.72)}点(周三3倍)")
    results = cerebro.run()
    strat = results[0]

    # ---- 统计 ----
    ret = strat.analyzers.returns.get_analysis()
    dd = strat.analyzers.dd.get_analysis()
    ta = strat.analyzers.ta.get_analysis()
    sharpe = strat.analyzers.sharpe.get_analysis()

    final = cerebro.broker.getvalue()
    initial = cash
    total_ret = final / initial - 1.0
    days = (df15.index[-1] - df15.index[0]).days
    years = max(days, 1) / 365.0
    ann = (1.0 + total_ret) ** (1.0 / years) - 1.0 if total_ret > -1 else -1.0

    closed = ta.total.closed if hasattr(ta.total, "closed") else 0
    won = ta.won.total if hasattr(ta.won, "total") else 0
    lost = ta.lost.total if hasattr(ta.lost, "total") else 0
    win_rate = won / closed if closed > 0 else 0.0

    print("=" * 56)
    print(f"XAU/USD 箱体通道策略回测结果 ({freq})")
    print("=" * 56)
    print(f"区间            : {df15.index[0]} ~ {df15.index[-1]}  ({days} 天)")
    print(f"最终资金        : {final:,.2f}")
    print(f"累计收益        : {total_ret*100:+.2f}%")
    print(f"年化收益(自算)  : {ann*100:+.2f}%")
    print(f"最大回撤        : {dd.max.drawdown:.2f}%")
    print(f"Sharpe(年化)    : {sharpe.get('sharperatio', float('nan'))}")
    print(f"平仓交易数      : {closed}  (胜 {won} / 负 {lost})")
    print(f"胜率            : {win_rate*100:.1f}%")
    print(f"策略入场次数    : {strat.n_entries}")
    print(f"开/平仓标记数   : {len(strat.trades_log)}")
    print(f"累计隔夜费      : {strat.swap_total:+.2f} USD (多单-72.5/空单+30.72 点/晚, 周三3倍)")
    print("=" * 56)

    # 资金曲线 (带 datetime, 供可视化对齐)
    eq_df = pd.DataFrame(strat.equity, columns=["datetime", "value"])
    metrics = dict(final=final, total_ret=total_ret, ann=ann,
                   maxdd=dd.max.drawdown, closed=closed, won=won,
                   win_rate=win_rate, entries=strat.n_entries,
                   swap_total=strat.swap_total)
    return eq_df, strat.trades_log, metrics


# --------------------------------------------------------------------------
# 命令行入口
# --------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", default="15m", choices=["15m", "1h"])
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--cash", type=float, default=1000.0, help="本金 USD (TMGM 账户)")
    ap.add_argument("--leverage", type=float, default=1000.0, help="杠杆倍数 1:N")
    ap.add_argument("--lot", type=float, default=0.01, help="每笔手数 (0.01 手=1盎司)")
    ap.add_argument("--spread", type=float, default=0.33, help="点差 美元/盎司 (33 points)")
    ap.add_argument("--commission", type=float, default=0.0, help="单边佣金(比例)")
    ap.add_argument("--swap-long", type=float, default=-72.5, help="多单隔夜费(点)")
    ap.add_argument("--swap-short", type=float, default=30.72, help="空单隔夜费(点,正=返息)")
    ap.add_argument("--r-mult", type=float, default=1.2)
    ap.add_argument("--sl-buffer", type=float, default=2.0)
    ap.add_argument("--trend-window", type=int, default=24)
    ap.add_argument("--short-win", type=int, default=8)
    ap.add_argument("--long-win", type=int, default=4)
    ap.add_argument("--min-k", type=int, default=3)
    ap.add_argument("--rev-filter", action="store_true")
    args = ap.parse_args()

    extra = dict(
        print_log=args.log,
        r_mult=args.r_mult,
        sl_buffer=args.sl_buffer,
        trend_window=args.trend_window,
        short_win=args.short_win,
        long_win=args.long_win,
        min_k=args.min_k,
        use_reversal_filter=args.rev_filter,
        swap_long_points=args.swap_long,
        swap_short_points=args.swap_short,
    )
    run_backtest(freq=args.freq, plot=args.plot, extra_args=extra,
                 cash=args.cash, leverage=args.leverage, lot=args.lot,
                 spread=args.spread, commission=args.commission)
