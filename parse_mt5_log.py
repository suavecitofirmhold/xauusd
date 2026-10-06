# -*- coding: utf-8 -*-
"""
从 MT5 测试器运行日志重建逐笔成交, 并与回测端 scalp_trades_record.csv 做对拍。
只取日志里"from 2025.06.01 00:00 to 2026.08.29"那次运行(第9次, 与回测 TEST 窗口一致)。
deal 行格式: ... Trades 2025.06.01 07:00:00  deal #2 buy 0.01 XAUUSD at 2634.21 done ...
注意: 日志时间为 MT5 服务器时间(含 DST), 回测 CSV 为 UTC, 二者有 2~3h 时区差 -> 逐笔时间不可直接对齐,
      故对拍以"聚合指标(笔数/胜率/收益/PF)"为主, 逐笔以"方向+序列"辅助。
"""
import codecs, re, csv
from datetime import datetime

LOG = r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000\logs\20260914.log"
TARGET = "from 2025.06.01 00:00 to 2026.08.29 00:00"
OUT = r"D:\workbuddy\tushare\XAUUSD\xauusd策略\mt5_tester_trades.csv"
CSV = r"D:\workbuddy\tushare\XAUUSD\xauusd策略\scalp_trades_record.csv"

deal_re = re.compile(r"deal #(\d+) (buy|sell) ([\d.]+) XAUUSD at ([\d.]+) done")

in_target = False
pos = None
trades = []
oid = 0
for ln in codecs.open(LOG, "r", "utf-16"):
    if TARGET in ln:
        in_target = True
        pos = None
        continue
    if not in_target:
        continue
    m = deal_re.search(ln)
    if not m:
        continue
    side, price = m.group(2), float(m.group(4))
    parts = ln.split()
    idx = parts.index("Trades")
    dt = datetime.strptime(parts[idx + 1] + " " + parts[idx + 2], "%Y.%m.%d %H:%M:%S")
    if pos is None:
        pos = dict(side=side, entry_dt=dt, entry=price)
    else:
        sign = 1 if pos["side"] == "buy" else -1
        pnl = sign * (price - pos["entry"]) * 1.0  # 0.01 手 = 1 oz
        oid += 1
        trades.append(dict(
            id=oid, side=pos["side"],
            entry_dt=pos["entry_dt"].strftime("%Y-%m-%d %H:%M"),
            exit_dt=dt.strftime("%Y-%m-%d %H:%M"),
            entry_price=round(pos["entry"], 2), exit_price=round(price, 2),
            pnl_usd=round(pnl, 2)))
        pos = None

n = len(trades)
wins = [t for t in trades if t["pnl_usd"] > 0]
losses = [t for t in trades if t["pnl_usd"] <= 0]
gp = sum(t["pnl_usd"] for t in wins)
gl = -sum(t["pnl_usd"] for t in losses)
pf = gp / gl if gl > 0 else float("inf")
ret = (gp - gl) / 1000 * 100.0

print("=== MT5 测试器(2025.06.01~2026.08.29) 重建结果 ===")
print(f"成交 {n} 笔 | 胜 {len(wins)} / 负 {len(losses)} | 胜率 {len(wins)/n*100:.1f}%")
print(f"总盈亏 {gp-gl:+.1f} USD | 收益 {ret:+.1f}% | PF {pf:.2f}")

with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["id", "side", "entry_dt", "exit_dt", "entry_price", "exit_price", "pnl_usd"])
    w.writeheader()
    w.writerows(trades)
print("已写出", OUT)

# 读回测 CSV 做对比
import csv as _csv
with open(CSV, encoding="utf-8-sig") as f:
    rows = list(_csv.DictReader(f))
cn = len(rows)
cwins = sum(1 for r in rows if float(r["pnl_usd"]) > 0)
cgp = sum(float(r["pnl_usd"]) for r in rows if float(r["pnl_usd"]) > 0)
cgl = -sum(float(r["pnl_usd"]) for r in rows if float(r["pnl_usd"]) <= 0)
cpf = cgp / cgl if cgl > 0 else float("inf")
cret = (cgp - cgl) / 1000 * 100.0

print("\n=== 对拍: MT5测试器 vs 回测 scalp_trades_record.csv ===")
print(f"{'指标':<10}{'MT5测试器':>14}{'回测':>14}{'偏差':>12}")
print(f"{'成交笔数':<10}{n:>14}{cn:>14}{n-cn:>+12}")
print(f"{'胜率%':<10}{len(wins)/n*100:>13.1f}{cwins/cn*100:>13.1f}{len(wins)/n*100-cwins/cn*100:>+11.1f}")
print(f"{'收益%':<10}{ret:>13.1f}{cret:>13.1f}{ret-cret:>+11.1f}")
print(f"{'PF':<10}{pf:>14.2f}{cpf:>14.2f}{pf-cpf:>+12.2f}")
print("\n结论: 若偏差在合理范围(笔数±10、胜率±3pp、收益±5pp、PF±0.1), 即 EA↔回测对齐成功。")
