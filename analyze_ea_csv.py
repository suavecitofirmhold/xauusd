# -*- coding: utf-8 -*-
"""
解析 MT5 EA 生成的 BoxChannelScalp_trades.csv，计算真实收益/PF/胜率/回撤。
注意 CSV 里 eod 关闭和 broker 关闭可能同一时间点重复记录，需要去重。
"""
import csv, sys
from collections import defaultdict

def analyze(path):
    rows = list(csv.DictReader(open(path, newline='', encoding='utf-8')))
    print(f"原始行数: {len(rows)}")

    # 去重：同一时间点连续出现多次 close，保留 reason=='broker' 或非 0 profit 的那条
    cleaned = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if r['action'] == 'open':
            cleaned.append(r)
            i += 1
        elif r['action'] == 'close':
            # 收集这一组连续同 side 的 close
            group = [r]
            j = i + 1
            while j < len(rows) and rows[j]['action'] == 'close' and rows[j]['side'] == r['side']:
                group.append(rows[j])
                j += 1
            # 选择：优先 reason==broker 且 profit 非空；否则 profit 最大那条；否则第一条
            best = None
            for c in group:
                try:
                    c['profit_f'] = float(c['profit'])
                except ValueError:
                    c['profit_f'] = 0.0
            brokers = [c for c in group if c.get('reason') == 'broker']
            if brokers:
                best = max(brokers, key=lambda x: abs(x['profit_f']))
            else:
                best = max(group, key=lambda x: abs(x['profit_f']))
            cleaned.append(best)
            i = j
        else:
            cleaned.append(r)
            i += 1

    opens = [r for r in cleaned if r['action'] == 'open']
    closes = [r for r in cleaned if r['action'] == 'close']
    print(f"open={len(opens)} close={len(closes)}")

    if len(opens) != len(closes):
        print(f"⚠️ open/close 数量不一致，可能期末仍有持仓或未配对")

    balance = 1000.0
    peak = balance
    maxdd = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    losses = 0

    # 配对：简单按顺序 open[i] -> close[i]
    n = min(len(opens), len(closes))
    paired = []
    for o, c in zip(opens, closes):
        pnl = float(c['profit'])
        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak
        maxdd = max(maxdd, dd)
        if pnl >= 0:
            gross_profit += pnl
            wins += 1
        else:
            gross_loss += -pnl
            losses += 1
        paired.append((o['time'], c['time'], o['side'], pnl, balance))

    total_ret = balance / 1000.0 - 1.0
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    win_rate = wins / n if n > 0 else 0.0

    print(f"\n=== EA 真实执行统计 ===")
    print(f"初始入金: 1000.00")
    print(f"最终余额: {balance:.2f}")
    print(f"总收益:   {total_ret*100:+.2f}%")
    print(f"最大回撤: {maxdd*100:.2f}%")
    print(f"交易笔数: {n}")
    print(f"胜率:     {win_rate*100:.1f}%")
    print(f"毛利:     {gross_profit:.2f}")
    print(f"毛损:     {gross_loss:.2f}")
    print(f"盈利因子: {pf:.2f}")

    # 输出前 10 笔和后 10 笔供检查
    print("\n=== 首 5 笔 ===")
    for p in paired[:5]:
        print(f"{p[0]} -> {p[1]}  {p[2]:>5}  pnl={p[3]:+.2f}  bal={p[4]:.2f}")
    print("=== 末 5 笔 ===")
    for p in paired[-5:]:
        print(f"{p[0]} -> {p[1]}  {p[2]:>5}  pnl={p[3]:+.2f}  bal={p[4]:.2f}")

    return dict(balance=balance, total_ret=total_ret, maxdd=maxdd, trades=n,
                win_rate=win_rate, pf=pf, gross_profit=gross_profit, gross_loss=gross_loss)


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000\MQL5\Files\BoxChannelScalp_trades.csv"
    analyze(path)
