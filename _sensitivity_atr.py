# -*- coding: utf-8 -*-
"""ATR 止损倍数稳健性扫描(响应'刀刃效应'疑虑)。

对每个 atr_stop_mult 跑全窗口回测, 输出收益/PF/笔数/胜率/DD/Sharpe,
并量化"相邻参数跳变"以判断是否悬崖式(过拟合)分布。
同时跑 atr_shift=1(对齐EA)与 atr_shift=0(旧口径)两栏做对照。
"""
import sys
from box_channel_optimized import run_backtest, PROFILES

WIN_FROM, WIN_TO = "2024-03-04", "2026-08-26"
MULTS = [1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.0]


def run_one(mult, shift):
    args = dict(PROFILES["scalp"])
    args["atr_shift"] = shift
    args["atr_stop_mult"] = mult
    m = run_backtest(freq="15m", extra_args=args,
                     fromdate=WIN_FROM, todate=WIN_TO, quiet=True)
    return m


def scan(shift):
    out = []
    for mult in MULTS:
        m = run_one(mult, shift)
        out.append(dict(mult=mult, ret=m["total_ret"] * 100, pf=m["pf"],
                        trades=m["closed"], wr=m["win_rate"] * 100,
                        dd=m["maxdd"], sharpe=m["sharpe"],
                        net=m["gross_profit"] - m["gross_loss"]))
        print(f"  [shift={shift}] mult={mult:<4} ret={out[-1]['ret']:+7.2f}%  "
              f"PF={out[-1]['pf']:.3f}  n={out[-1]['trades']:>4}  "
              f"wr={out[-1]['wr']:.1f}%  DD={out[-1]['dd']:.2f}%", flush=True)
    return out


def show(title, rows):
    print()
    print("=" * 88)
    print(title)
    print("=" * 88)
    print(f"  {'mult':<6}{'收益%':>10}{'PF':>8}{'笔数':>7}{'胜率%':>8}{'DD%':>8}{'Sharpe':>9}{'净额$':>10}")
    print("  " + "-" * 84)
    for r in rows:
        print(f"  {r['mult']:<6.1f}{r['ret']:>+10.2f}{r['pf']:>8.3f}{r['trades']:>7}"
              f"{r['wr']:>8.1f}{r['dd']:>8.2f}{r['sharpe']:>9.2f}{r['net']:>+10.1f}")


def verdict(name, rows):
    print()
    print(f"--- {name} 稳健性判读 ---")
    rets = [r["ret"] for r in rows]
    pfs = [r["pf"] for r in rows]
    pos = sum(1 for v in rets if v > 0)
    print(f"  收益全为正的档位: {pos}/{len(rows)}   "
          f"范围 [{min(rets):+.2f}%, {max(rets):+.2f}%]   极差 {max(rets)-min(rets):.2f}pp")
    print(f"  PF 范围 [{min(pfs):.3f}, {max(pfs):.3f}]   全部>1.0: {all(v > 1.0 for v in pfs)}")
    # 相邻跳变(相对 mult 步长 0.1 归一化)
    jumps = []
    for a, b in zip(rows, rows[1:]):
        dm = b["mult"] - a["mult"]
        jumps.append((abs(b["ret"] - a["ret"]) / dm, a["mult"], b["mult"],
                      b["ret"] - a["ret"]))
    jumps.sort(reverse=True)
    print("  相邻跳变最大 3 处(每 0.1 mult 的收益变化):")
    for j, ma, mb, d in jumps[:3]:
        print(f"    {ma:.1f} -> {mb:.1f}: {d:+.2f}pp   ({j:.1f}pp per 0.1)")
    avg_jump = sum(j[0] for j in jumps) / len(jumps)
    print(f"  平均跳变: {avg_jump:.1f}pp per 0.1 mult")

    # 判定
    all_pos = pos == len(rows)
    pf_ok = all(v > 1.0 for v in pfs)
    max_jump = jumps[0][0]
    if all_pos and pf_ok and max_jump < 25:
        v = "✅ 稳健: 全档位盈利、PF 均>1、无悬崖"
    elif all_pos and pf_ok:
        v = "⚠️ 整体盈利但存在陡变区: 参数需留安全边际, 勿卡在最优点"
    elif pf_ok:
        v = "⚠️ PF 尚可读但部分档位亏损: 对倍数敏感, 实盘谨慎"
    else:
        v = "❌ 危险: 存在 PF<1 档位, 疑似过拟合, 不建议实盘"
    print(f"  判定: {v}")


if __name__ == "__main__":
    print(f"扫描窗口 {WIN_FROM} ~ {WIN_TO}, mult={MULTS}", flush=True)
    print("开始 shift=1(对齐 EA GetATRVal(1)) 扫描...", flush=True)
    r1 = scan(1)
    print("开始 shift=0(backtrader 旧口径) 扫描...", flush=True)
    r0 = scan(0)

    show("【主表】atr_shift=1  —— 与 EA GetATRVal(1) 对齐", r1)
    show("【对照】atr_shift=0  —— backtrader 默认口径(偏乐观)", r0)

    print()
    print("=" * 88)
    print("两种口径差异 (shift=1 - shift=0) —— 检验'最优值'是否随口径漂移")
    print("=" * 88)
    print(f"  {'mult':<6}{'Δ收益pp':>12}{'ΔPF':>10}{'Δ笔数':>9}")
    print("  " + "-" * 40)
    for a, b in zip(r1, r0):
        print(f"  {a['mult']:<6.1f}{a['ret']-b['ret']:>+12.2f}{a['pf']-b['pf']:>+10.3f}"
              f"{a['trades']-b['trades']:>+9}")

    verdict("atr_shift=1(对齐EA)", r1)
    verdict("atr_shift=0(旧口径)", r0)
