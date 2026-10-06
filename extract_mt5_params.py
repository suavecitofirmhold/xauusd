# -*- coding: utf-8 -*-
"""
扫描 MT5 测试器日志, 按运行分段抓取 EA 输入参数(Inp*).
重点确认 Run9(最后一次运行, 2025.06.01~2026.08.29) 是否含
InpRevStrongOnly / InpRevStrongThresh —— 决定对拍该用 rev_in_strong(+100%)
还是 always_on 常开(+79.25%) 作为回测基准.
"""
import codecs, re

LOG = r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000\logs\20260914.log"

cooldown_re = re.compile(r"InpCooldownBars=(\d+)")
param_re = re.compile(r"(Inp\w+)=([^\s]+)")
period_re = re.compile(r"from (\d{4}\.\d{2}\.\d{2}) \d{2}:\d{2} to (\d{4}\.\d{2}\d{0,2})")

runs = []
pending_period = None
cur = None
with codecs.open(LOG, "r", "utf-16") as f:
    for line in f:
        cm = cooldown_re.search(line)
        if cm:
            cur = {"cooldown": cm.group(1), "params": {}, "period": pending_period}
            pending_period = None
            runs.append(cur)
            continue
        pe = period_re.search(line)
        if pe:
            txt = f"{pe.group(1)}~{pe.group(2)}"
            if cur is not None:
                cur["period"] = txt
            else:
                pending_period = txt
            continue
        if cur is None:
            continue
        pm = param_re.search(line)
        if pm:
            cur["params"][pm.group(1)] = pm.group(2)

print(f"检测到运行次数: {len(runs)}")
for i, r in enumerate(runs, 1):
    print(f"\n--- Run{i}: cooldown={r['cooldown']} period={r['period']}")
    # 关键参数
    focus = ["InpRsiOverbought", "InpRsiOversold", "InpAtrStopMult",
             "InpUseServerStops", "InpUseReversalFilter", "InpRevStrongOnly",
             "InpRevStrongThresh", "InpUseReversal"]
    for k in focus:
        if k in r["params"]:
            print(f"    {k} = {r['params'][k]}")
    # 任何含 Rev/Strong/Server 的参数(抓别名)
    extra = [f"{k}={v}" for k, v in r["params"].items()
             if any(x in k for x in ["Rev", "Strong", "Server", "Reversal"])]
    if extra:
        print("    REV/SERVER 相关:", extra)

print("\n===== 最后一次运行(Run9, 即对拍对象)完整参数块 =====")
last = runs[-1]
for k, v in last["params"].items():
    print(f"    {k} = {v}")

# 全局确认 InpRevStrongOnly 是否出现过
all_keys = set()
for r in runs:
    all_keys.update(r["params"].keys())
print("\n===== 全局出现过的 Inp* 参数键 =====")
print("    " + ", ".join(sorted(all_keys)))
print("\n=> InpRevStrongOnly 是否存在:", "InpRevStrongOnly" in all_keys)
print("=> InpRevStrongThresh 是否存在:", "InpRevStrongThresh" in all_keys)
