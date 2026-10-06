# -*- coding: utf-8 -*-
"""
XAUUSD M5 均值回归策略 v2 —— 由实测发现重构, 不是继续调参。

## 为什么要重写(来自 m5_clean_select / m5_session_test 的实测)
1. **调参无预测力**: IS→OOS 秩相关 ρ≈0, 继续扫参是徒劳 → 必须改**结构**而非参数。
2. **优势不在高波动段**: 伦敦/纽约重叠(服务器 13-19)IS PF 0.94 / OOS 0.83 两段都亏;
   核心 15-18 也是 0.93/0.86。而全时段 1-22 是 1.14/1.15、白天 8-20 是 1.09/1.15。
   → 说明所谓"突破"在安静市场里实际表现为**区间行为**, 高波动段反而是假突破洗盘。
3. **时段维度 IS→OOS 高度一致**(结构性效应可迁移), 与参数维度形成鲜明对比。

## v2 的设计(与 v1 的"突破"相反)
- **只做回归, 不做突破**: 在区间沿反向入场, 目标对侧沿或中轨。
- **波动率闸门**: 只在 atr/atr_avg ≤ vol_max 的"清淡市"交易(高波动段直接回避)。
- **时段回避**: 默认屏蔽服务器 13-19(伦敦纽约重叠)。
- **可选趋势对齐**: trend_align=1 时只顺 1h 趋势方向做回归(回踩), 0=纯双向回归。

## 复用约定(同 v1)
- 继承 BoxChannelOptStrategy 复用撮合机械; notify_order 保持 pass; SL/TP 为 broker 端挂单
- 所有距离用 ATR 相对值; 日界/时段由 bt.num2date() 推导
- 以"实际成交价"重建 SL/TP(根除止损反向)

用法:
  python xauusd_m5_meanrev.py --from 2025-04-15 --to 2026-09-11
  python xauusd_m5_meanrev.py --profile mr_quiet --from 2025-04-15 --to 2025-12-31
"""
import argparse

import numpy as np
import pandas as pd
import backtrader as bt

from box_channel_optimized import (
    BoxChannelOptStrategy, FixedLotSizer, load_data, tmgm_server_offset_hours,
)
from xauusd_m5_multisignal import ols_fit, server_hour, bars_for_minutes


class M5MeanReversionStrategy(BoxChannelOptStrategy):
    params = dict(
        # ---------- 时段 ----------
        trade_start_hour=1, trade_end_hour=22,
        avoid_start_hour=13, avoid_end_hour=19,   # 屏蔽窗口(起==止 表示不屏蔽)
        # ---------- 波动率闸门(核心: 只在清淡市交易) ----------
        vol_max=1.2,          # atr/atr_avg 上限, 超过说明太躁动 → 不做
        vol_min=0.0,          # 下限(避免死水)
        # ---------- 区间 ----------
        rg_minutes=240,       # 区间回看(分钟口径)
        rg_min_span_atr=1.0,  # 区间最小跨度
        rg_max_span_atr=6.0,  # 区间最大跨度(太宽不算震荡)
        fade_tol_atr=0.15,    # 触沿容差
        # ---------- 触发/确认 ----------
        rsi_os=35.0, rsi_ob=65.0,
        require_reversal=True,
        trend_align=0,        # 0=纯双向回归; 1=只顺 1h 趋势方向(即回踩); -1=只逆势
        # ---------- 出场 ----------
        stop_buf_atr=0.5,     # 止损放在区间沿外 buffer×ATR
        stop_min_atr=0.4,
        stop_max_atr=1.6,
        k_target=1.2,         # 最低 R 倍数
        use_midline=True,     # 目标优先取中轨
        max_hold_bars=36,
        # ---------- 风控 ----------
        max_trades_day=99,
        max_daily_loss_r=99.0,
        cooldown_bars=4,
        min_risk_usd=2.0,
        min_target_usd=2.0,
        max_spread_tail=20.0,
        quality_gate=False,
        # ---------- 追踪 ----------
        trail_activate_atr=0.8, trail_atr_mult=0.9, trail_be_buffer=0.15,
        atr_shift=1,
        eod_lead_hours=2,
        no_trade_before="",
        print_rejects=False,
    )

    def __init__(self):
        super().__init__()
        d = self.data
        self.ema20_1h = bt.indicators.EMA(self.datas[2].close, period=20)
        self._regime_key = None
        self._rel_1h = 0.0
        self.rejects = __import__("collections").Counter()
        self._day = None
        self._trades_today = 0
        self._pnl_r_today = 0.0
        self._last_entry_bar = -9999
        self._hold_bars = 0
        self._pending_cooldown = int(self.p.cooldown_bars)
        self._no_trade_before = (pd.Timestamp(self.p.no_trade_before).date()
                                 if self.p.no_trade_before else None)

    # ------------------------------------------------------------------ 辅助
    def _rej(self, k):
        self.rejects[k] += 1

    def _now_dt(self):
        return bt.num2date(self.data.datetime[0])

    def _server_hour(self, dt):
        return server_hour(dt, tmgm_server_offset_hours(dt))

    def _atr_now(self):
        return self.atr[-self.p.atr_shift]

    def _calc_rel_1h(self):
        """1h 通道斜率/均值(缓存, 只在 1h bar 前进时重算)。"""
        key = len(self.datas[2])
        if key == self._regime_key:
            return self._rel_1h
        self._regime_key = key
        try:
            ys = [self.datas[2].close[-i] for i in range(24, 0, -1)]
        except Exception:
            ys = []
        if len(ys) >= 10 and min(ys) > 0:
            mean = sum(ys) / len(ys)
            b, a, _ = ols_fit(list(range(len(ys))), ys)
            self._rel_1h = (b / mean) if mean else 0.0
        else:
            self._rel_1h = 0.0
        return self._rel_1h

    def _roll_day(self, dt):
        if self._day != dt.date():
            self._day = dt.date()
            self._trades_today = 0
            self._pnl_r_today = 0.0

    # ------------------------------------------------------------------ 信号
    def _signal(self, atr, c):
        """区间沿反向(均值回归)。返回 (dir, entry_ref, stop, target, R) 或 None。"""
        nb = bars_for_minutes(self.p.rg_minutes, 5)
        # 排除信号 bar 自身
        try:
            hi = max([self.data.high[-i] for i in range(2, nb + 2)])
            lo = min([self.data.low[-i] for i in range(2, nb + 2)])
        except Exception:
            return None
        span = hi - lo
        if span < self.p.rg_min_span_atr * atr or span > self.p.rg_max_span_atr * atr:
            self._rej("span")
            return None
        tol = self.p.fade_tol_atr * atr
        rsi = self.rsi[-1]
        mid = (hi + lo) / 2.0

        # 1h 趋势方向(用于 trend_align)
        rel = self._calc_rel_1h()
        trend_up = rel > 0.0

        # ---- 摸底做多 ----
        if self.data.low[-1] <= lo + tol and c > lo:
            if self.p.trend_align == 1 and not trend_up:
                self._rej("align")
                return None
            if self.p.trend_align == -1 and trend_up:
                self._rej("align")
                return None
            ok_rsi = rsi < self.p.rsi_os
            ok_rev = (not self.p.require_reversal) or self._bull_reject(lo, atr)
            if not (ok_rsi or ok_rev):
                self._rej("no_trigger")
                return None
            stop = lo - self.p.stop_buf_atr * atr
            risk = abs(c - stop)
            risk = min(max(risk, self.p.stop_min_atr * atr), self.p.stop_max_atr * atr)
            stop = c - risk
            target = self._pick_target(c, risk, mid, hi, 1)
            if target is None:
                return None
            return (1, c, stop, target, (target - c) / risk)

        # ---- 摸顶做空 ----
        if self.data.high[-1] >= hi - tol and c < hi:
            if self.p.trend_align == 1 and trend_up:
                self._rej("align")
                return None
            if self.p.trend_align == -1 and not trend_up:
                self._rej("align")
                return None
            ok_rsi = rsi > self.p.rsi_ob
            ok_rev = (not self.p.require_reversal) or self._bear_reject(hi, atr)
            if not (ok_rsi or ok_rev):
                self._rej("no_trigger")
                return None
            stop = hi + self.p.stop_buf_atr * atr
            risk = abs(stop - c)
            risk = min(max(risk, self.p.stop_min_atr * atr), self.p.stop_max_atr * atr)
            stop = c + risk
            target = self._pick_target(c, risk, mid, lo, -1)
            if target is None:
                return None
            return (-1, c, stop, target, (c - target) / risk)

        self._rej("no_touch")
        return None

    def _pick_target(self, c, risk, mid, far, d):
        """优先中轨, 其次对侧沿, 最后 k×risk; 必须 ≥ k_target R。"""
        cands = []
        if self.p.use_midline:
            cands.append(mid)
        cands.append(far)
        for t in cands:
            move = (t - c) if d == 1 else (c - t)
            if move >= self.p.k_target * risk:
                return t
        return c + d * self.p.k_target * risk

    def _bull_reject(self, level, atr):
        try:
            o, h, l, cl = (self.data.open[-1], self.data.high[-1],
                           self.data.low[-1], self.data.close[-1])
        except Exception:
            return False
        rng = h - l
        if rng <= 0:
            return False
        near = abs(cl - level) <= 0.3 * atr
        lower = min(o, cl) - l
        hammer = (lower / rng >= 0.5) and (cl >= o)
        po, pc = self.data.open[-2], self.data.close[-2]
        engulf = (pc < po) and (cl > o) and (cl > po)
        return near and (hammer or engulf)

    def _bear_reject(self, level, atr):
        try:
            o, h, l, cl = (self.data.open[-1], self.data.high[-1],
                           self.data.low[-1], self.data.close[-1])
        except Exception:
            return False
        rng = h - l
        if rng <= 0:
            return False
        near = abs(cl - level) <= 0.3 * atr
        upper = h - max(o, cl)
        star = (upper / rng >= 0.5) and (cl <= o)
        po, pc = self.data.open[-2], self.data.close[-2]
        engulf = (pc > po) and (cl < o) and (cl < po)
        return near and (star or engulf)

    # ------------------------------------------------------------------ 主循环
    def next(self):
        dt = self._now_dt()
        self._charge_swap()
        self.equity.append((dt, self.broker.getvalue()))
        self._roll_day(dt)
        hr = self._server_hour(dt)

        # 1) 平仓检测
        for o, reason in ((self.sl_order, "sl"), (self.tp_order, "tp"),
                          (self.manual_order, "eod")):
            if o is not None and getattr(o, "status", None) == bt.Order.Completed:
                self._log_close(reason, o)
                self._finalize_close()
                return

        # 2) 成交后挂 SL/TP
        if self.position and self.sl_order is None and self.tp_order is None and self.trade_info:
            self._attach_sl_tp()
            self._hold_bars = 0

        # 3) 持仓管理
        if self.position:
            if self._margin_call():
                self._log_close("margin", None)
                self._finalize_close()
                return
            if self.p.trailing:
                self._maybe_trail()
            self._hold_bars += 1
            if self.p.max_hold_bars and self._hold_bars >= self.p.max_hold_bars:
                self._manual_close()
                return
            if self.p.avoid_overnight and hr >= (self.p.server_close_hour - self.p.eod_lead_hours):
                self._manual_close()
                return
            return

        # 4) 开仓评估
        if self._no_trade_before and dt.date() < self._no_trade_before:
            return
        atr = self._atr_now()
        if not atr or atr <= 0:
            return
        c = self.data.close[-1]

        # 时段(含屏蔽窗口)
        if hr < self.p.trade_start_hour or hr >= self.p.trade_end_hour:
            self._rej("session")
            return
        if self.p.avoid_start_hour != self.p.avoid_end_hour:
            if self.p.avoid_start_hour <= hr < self.p.avoid_end_hour:
                self._rej("avoid_window")
                return

        # 波动率闸门(核心: 只在清淡市做回归)
        atr_avg = self.atr_avg[-self.p.atr_shift] if len(self.atr_avg) else 0.0
        ratio = (atr / atr_avg) if (atr_avg and atr_avg > 0) else 1.0
        if ratio > self.p.vol_max or ratio < self.p.vol_min:
            self._rej("vol_gate")
            return

        # 点差尾部 + 冷却 + 日内上限
        try:
            sp = self.current_spread_points(dt)
            if sp and sp > self.p.max_spread_tail:
                self._rej("spread")
                return
        except Exception:
            pass
        if len(self.data) - self._last_entry_bar < self._pending_cooldown:
            self._rej("cooldown")
            return
        if self._trades_today >= self.p.max_trades_day:
            self._rej("max_trades_day")
            return
        if self._pnl_r_today <= -abs(self.p.max_daily_loss_r):
            self._rej("daily_loss")
            return

        sig = self._signal(atr, c)
        if not sig:
            return
        d, entry_ref, stop, target, R = sig
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
        self._open_position("long" if d == 1 else "short", entry_ref, stop, target,
                            R, self.rsi[-1])

    # ------------------------------------------------------------------ 收尾
    def _open_position(self, want, entry_ref, stop, target, rm, rsi):
        super()._open_position(want, entry_ref, stop, target, rm, rsi)
        self.trade_info["risk"] = abs(entry_ref - stop)

    def _attach_sl_tp(self):
        info = self.trade_info
        fill = self.position.price if self.position else info.get("entry")
        risk, k, d = info.get("risk"), info.get("r_mult"), info.get("dir")
        if fill and risk and risk > 0 and k and d in (1, -1):
            if d == 1:
                info["stop"] = fill - risk
                info["target"] = fill + k * risk
            else:
                info["stop"] = fill + risk
                info["target"] = fill - k * risk
        super()._attach_sl_tp()

    def _finalize_close(self):
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
        self._pending_cooldown = int(self.p.cooldown_bars)
        self._last_entry_bar = len(self.data)
        self._reset_pos()
        self._hold_bars = 0


# ============================ 运行器 ============================

def run_mr_backtest(extra_args=None, cash=1000.0, leverage=1000.0, lot=0.01,
                    spread=0.33, commission=0.0,
                    fromdate=None, todate=None, quiet=True,
                    primary_freq="5m", context_freqs=("15m", "1h")):
    cerebro = bt.Cerebro()
    cerebro.addsizer(FixedLotSizer, lot=lot)
    df_primary, data_primary, sp_primary = load_data(primary_freq, fromdate, todate)
    cerebro.adddata(data_primary, name=primary_freq)
    for cf in context_freqs:
        _, data_cf, _ = load_data(cf, fromdate, todate)
        cerebro.adddata(data_cf, name=cf)
    cerebro.addstrategy(M5MeanReversionStrategy, spread_series=sp_primary,
                        **(extra_args or {}))
    cerebro.broker.addcommissioninfo(
        bt.CommissionInfo(commission=commission, mult=100.0,
                          leverage=leverage, stocklike=False))
    cerebro.broker.setcash(cash)
    cerebro.broker.set_slippage_fixed(fixed=spread / 2.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    res = cerebro.run(optreturn=False)
    strat = res[0]
    dd = strat.analyzers.dd.get_analysis()

    final = cerebro.broker.getvalue()
    total_ret = final / cash - 1.0
    days = (df_primary.index[-1] - df_primary.index[0]).days
    years = max(days, 1) / 365.0
    ann = (1.0 + total_ret) ** (1.0 / years) - 1.0 if total_ret > -1 else -1.0

    stack, paired = [], []
    for t in strat.trades_log:
        if t["kind"] == "open":
            stack.append(t)
        elif t["kind"] == "close" and stack:
            paired.append((stack.pop(), t))
    gp = gl = 0.0
    won = 0
    rsum = 0.0
    nl = ns = 0
    gpl = gll = gps = gls = 0.0
    for o, c in paired:
        sign = 1 if o["side"] == "long" else -1
        pnl = (c["price"] - o["price"]) * sign
        risk = abs(o["price"] - (o["stop"] or o["price"]))
        if risk > 0:
            rsum += pnl / risk
        if pnl >= 0:
            gp += pnl
            won += 1
            gpl += pnl if sign == 1 else 0.0
            gps += pnl if sign == -1 else 0.0
        else:
            gl += -pnl
            gll += -pnl if sign == 1 else 0.0
            gls += -pnl if sign == -1 else 0.0
        if sign == 1:
            nl += 1
        else:
            ns += 1
    closed = len(paired)
    from collections import Counter
    trade_days = Counter(c["dt"].date() for o, c in paired)
    all_days = pd.Index(df_primary.index).normalize().unique()
    n_days = max(len(all_days), 1)
    out = dict(
        final=final, total_ret=total_ret, ann=ann,
        maxdd=dd.max.drawdown if hasattr(dd, "max") else 0.0,
        closed=closed, won=won, lost=closed - won,
        win_rate=(won / closed) if closed else 0.0,
        gross_profit=gp, gross_loss=gl,
        pf=(gp / gl) if gl > 0 else float("inf"),
        avg_R=(rsum / closed) if closed else 0.0,
        expectancy=(gp - gl) / closed if closed else 0.0,
        trades_per_day=closed / n_days if n_days else 0.0,
        pct_days_traded=len(trade_days) / n_days if n_days else 0.0,
        n_long=nl, n_short=ns,
        pf_long=(gpl / gll) if gll > 0 else float("inf"),
        pf_short=(gps / gls) if gls > 0 else float("inf"),
        n_days=int(n_days), entries=strat.n_entries,
        swap_total=getattr(strat, "swap_total", 0.0),
        eq=getattr(strat, "equity", []), trades=strat.trades_log,
        reason_hist=dict(Counter(c.get("reason") for o, c in paired)),
        rejects=dict(getattr(strat, "rejects", {})),
    )
    if not quiet:
        print(f"[MR] ret={total_ret*100:+.2f}% ann={ann*100:+.1f}% PF={out['pf']:.2f} "
              f"WR={out['win_rate']*100:.1f}% closed={closed} "
              f"trades/day={out['trades_per_day']:.2f} "
              f"days={out['pct_days_traded']*100:.0f}% DD={out['maxdd']:.1f}% "
              f"final=${final:.2f}")
        print(f"     long={nl}(PF {out['pf_long']:.2f}) short={ns}(PF {out['pf_short']:.2f}) "
              f"avg_R={out['avg_R']:.2f} exp=${out['expectancy']:.2f}")
        if out["rejects"]:
            top = sorted(out["rejects"].items(), key=lambda kv: -kv[1])[:5]
            print("     拒绝TOP: " + ", ".join(f"{k}={v}" for k, v in top))
    return out


MR_PROFILES = {
    # 纯双向均值回归 + 屏蔽重叠段
    "mr_quiet": dict(avoid_start_hour=13, avoid_end_hour=19, vol_max=1.2,
                     trend_align=0),
    # 只顺 1h 趋势做回踩
    "mr_trend": dict(avoid_start_hour=13, avoid_end_hour=19, vol_max=1.2,
                     trend_align=1),
    # 更严格: 只在很低波动时做
    "mr_vq": dict(avoid_start_hour=13, avoid_end_hour=19, vol_max=0.9,
                  trend_align=0),
    # 不屏蔽时段(对照)
    "mr_full": dict(avoid_start_hour=0, avoid_end_hour=0, vol_max=1.2,
                    trend_align=0),
}


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2025-04-15")
    ap.add_argument("--to", dest="to", default="2026-09-11")
    ap.add_argument("--profile", default="mr_quiet")
    ap.add_argument("--cash", type=float, default=1000.0)
    ap.add_argument("--spread", type=float, default=0.33)
    a = ap.parse_args()
    run_mr_backtest(extra_args=dict(MR_PROFILES.get(a.profile, {})),
                    cash=a.cash, spread=a.spread,
                    fromdate=a.frm, todate=a.to, quiet=False)


if __name__ == "__main__":
    _main()
