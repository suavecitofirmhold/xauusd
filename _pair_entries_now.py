import sys, os, csv, datetime, statistics
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
import box_channel_optimized as M
from datetime import timedelta

OUT = r"D:\workbuddy\tushare\XAUUSD\xauusd策略\_py_trades_sw8.csv"
EA_CSV = r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000\MQL5\Files\BoxChannelScalp_trades.csv"

print(">> running Python backtest (scalp, atr_shift=1, short_win=8) ...")
res = M.run_backtest(extra_args={**M.PROFILES['scalp'], 'atr_shift': 1})
tl = res['trades']
print(f"   Python: ret={res['total_ret']*100:+.1f}% trades={res['closed']} wr={res['win_rate']*100:.1f}% pf={res['pf']:.2f} eod={res['overnight_eod']}")

py_opens = [(t['dt'], t['side'], float(t['price'])) for t in tl if t['kind'] == 'open']
with open(OUT, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["dt", "side", "price", "kind", "reason", "stop", "target"])
    for t in tl:
        w.writerow([t['dt'], t['side'], t.get('price'), t.get('kind'), t.get('reason'), t.get('stop'), t.get('target')])
print("   dumped Python trades ->", OUT)

def to_utc(server_str):
    st = datetime.datetime.strptime(server_str, "%Y.%m.%d %H:%M:%S")
    for off in (3, 2):
        utc = st - timedelta(hours=off)
        if M.tmgm_server_offset_hours(utc) == off:
            return utc
    return st - timedelta(hours=3)

ea_rows = list(csv.DictReader(open(EA_CSV, encoding="ansi", errors="replace")))
ea_opens = []
for r in ea_rows:
    if r['action'] == 'open':
        ea_opens.append((to_utc(r['time']), r['side'], float(r['price'])))

print(f"EA opens={len(ea_opens)}  PY opens={len(py_opens)}")

matches = 0; mism_side = 0; no_py = 0
price_diffs = []
for eu, es, ep in ea_opens:
    hit = None
    for pu, ps, pp in py_opens:
        if abs((pu - eu).total_seconds()) <= 900 and ps == es:
            hit = (pu, ps, pp); break
    if hit:
        matches += 1
        price_diffs.append(abs(ep - hit[2]))
    else:
        found_any = False
        for pu, ps, pp in py_opens:
            if abs((pu - eu).total_seconds()) <= 900:
                found_any = True; mism_side += 1; break
        if not found_any:
            no_py += 1

print(f"matched (side+time±15m): {matches}")
print(f"same-time but DIFFERENT side: {mism_side}")
print(f"no Python entry within ±15m: {no_py}")
if price_diffs:
    print(f"entry-price diff USD: median={statistics.median(price_diffs):.3f} mean={statistics.mean(price_diffs):.3f} max={max(price_diffs):.3f}")
