# -*- coding: utf-8 -*-
"""捕获真实策略 notify_order 全序列, 定位"平盘卖单成交转空"的精确触发过程。"""
import sys, os
import importlib.util
import backtrader as bt
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "box_channel_optimized.py")
spec = importlib.util.spec_from_file_location("bco_ord", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules["bco_ord"] = mod
spec.loader.exec_module(mod)

ORDT = {bt.Order.Market:"MKT", bt.Order.Stop:"STOP", bt.Order.Limit:"LIMIT", bt.Order.StopLimit:"STPLMT"}

class DiagStrat(mod.BoxChannelOptStrategy):
    def __init__(self):
        super().__init__()
        self.ev = []   # (bar, action, ref, otype, isbuy, status, broker_size)
        self._bar = 0
    def next(self):
        self._bar = len(self.data) - 1
        super().next()
    def notify_order(self, order):
        st = order.status
        if st in (bt.Order.Submitted, bt.Order.Accepted, bt.Order.Completed, bt.Order.Canceled, bt.Order.Margin, bt.Order.Rejected):
            sz = self.broker.getposition(self.data).size
            self.ev.append((self._bar, "EVT", order.ref, ORDT.get(order.ordtype,"?"),
                            order.isbuy(), st, round(sz,4)))
    def buy(self, *a, **k):
        o = super().buy(*a, **k)
        self.ev.append((self._bar, "BUY", o.ref, ORDT.get(o.ordtype,"?"), True, o.status, round(self.broker.getposition(self.data).size,4)))
        return o
    def sell(self, *a, **k):
        o = super().sell(*a, **k)
        self.ev.append((self._bar, "SELL", o.ref, ORDT.get(o.ordtype,"?"), False, o.status, round(self.broker.getposition(self.data).size,4)))
        return o
    def close(self, *a, **k):
        o = super().close(*a, **k)
        self.ev.append((self._bar, "CLOSE", o.ref, ORDT.get(o.ordtype,"?"), o.isbuy(), o.status, round(self.broker.getposition(self.data).size,4)))
        return o

cerebro = bt.Cerebro()
cerebro.addsizer(mod.FixedLotSizer, lot=0.01)
df_primary, data_primary, sp_primary = mod.load_data("15m")
cerebro.adddata(data_primary, name="15m")
_, data_cf, _ = mod.load_data("1h")
cerebro.adddata(data_cf, name="1h")
extra = dict(lot=0.01, max_positions=1)
extra.update(mod.PROFILES["scalp"])
cerebro.addstrategy(DiagStrat, spread_series=sp_primary, **extra)
comminfo = bt.CommissionInfo(commission=0.0, mult=100.0, leverage=1000.0, stocklike=False)
cerebro.broker.addcommissioninfo(comminfo)
cerebro.broker.setcash(1000.0)
cerebro.broker.set_slippage_fixed(fixed=0.165)
res = cerebro.run(optreturn=False)
strat = res[0]

# 找出第一笔"卖出成交使 pos 从 >=0 变成 <0"的事件, 打印其前后 25 个事件
ev = strat.ev
for i, e in enumerate(ev):
    bar, act, ref, otype, isbuy, st, sz = e
    if act == "EVT" and st == bt.Order.Completed and (not isbuy) and sz < 0:
        # 前一个是平盘? 回溯找到 pos 从 0 到 -0.01 的转折
        # 检查前一 Completed 卖单时的 size
        print("发现空头成交事件 @ bar", bar, "ref", ref, "otype", otype, "sz", sz)
        lo = max(0, i-30); hi = min(len(ev), i+5)
        for j in range(lo, hi):
            print("  ", ev[j])
        break
else:
    print("未找到空头成交事件(可能没有幽灵空单?) 事件总数=", len(ev))
