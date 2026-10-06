# -*- coding: utf-8 -*-
"""定位幽灵平仓: 逐 bar 记录 trades_log close 与 broker 真实成交, 并捕获 _log_close 时 broker 持仓数量。"""
import sys, os
import importlib.util
import backtrader as bt
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "box_channel_optimized.py")
spec = importlib.util.spec_from_file_location("bco_diag2", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules["bco_diag2"] = mod
spec.loader.exec_module(mod)

class DiagStrat(mod.BoxChannelOptStrategy):
    def __init__(self):
        super().__init__()
        self.log_close_events = []   # (bar_i, reason, broker_pos_size_at_close, order_ref, order_isclose)
        self.real_close_bars = []     # bar_i of real broker trade close
        self._bar_i = 0
    def next(self):
        self._bar_i = len(self.data) - 1
        super().next()
    def _log_close(self, pos, reason, order):
        # 捕获此刻 broker 实际持仓数量(平仓后应为 0; 若非0 即幽灵)
        pos_size = self.broker.getposition(self.data).size
        oref = order.ref if order is not None else None
        self.log_close_events.append(dict(bar=self._bar_i, reason=reason,
                                           broker_size=pos_size, oref=oref))
        super()._log_close(pos, reason, order)
    def notify_trade(self, trade):
        if trade.isclosed:
            self.real_close_bars.append(self._bar_i)

# 重新实现 run 以注入 DiagStrat
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

# 逐 bar 计数
from collections import Counter, defaultdict
my_by_bar = Counter(e["bar"] for e in strat.log_close_events)
real_by_bar = Counter(strat.real_close_bars)
my_total = sum(my_by_bar.values())
real_total = sum(real_by_bar.values())
print(f"我的 close bar 数={len(my_by_bar)}  总={my_total}")
print(f"broker close bar 数={len(real_by_bar)}  总={real_total}")
print(f"幽灵 close 数 = {my_total - real_total}")

# 找出 broker 持仓数量 != 0 的 close 事件(即该 close 并未真正平掉 broker 仓位)
phantom = [e for e in strat.log_close_events if e["reason"] is not None and e["broker_size"] != 0]
print(f"_log_close 时 broker 持仓!=0 的事件数 = {len(phantom)}")
# 这些事件的 reason 分布
from collections import Counter as C2
print("其中 reason 分布:", dict(C2(e["reason"] for e in phantom)))
# 展示前 20 个幽灵事件
for e in phantom[:20]:
    print(f"  bar#{e['bar']} reason={e['reason']} broker_size={e['broker_size']:.4f}")
