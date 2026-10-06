# -*- coding: utf-8 -*-
"""
直接定位 TARGET 行("from 2025.06.01 00:00 to 2026.08.29 00:00"),
用"最近的 InpCooldownBars= 行"界定该运行边界, 抓出该运行完整参数,
一锤定音确认对拍对象是否 rev_in_strong 模式.
"""
import codecs, re

LOG = r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000\logs\20260914.log"
TARGET = "from 2025.06.01 00:00 to 2026.08.29 00:00"

cd_re = re.compile(r"InpCooldownBars=(\d+)")
param_re = re.compile(r"(Inp\w+)=([^\s]+)")
period_re = re.compile(r"from \d{4}\.\d{2}\.\d{2} \d{2}:\d{2}(:\d{2})? to \d{4}\.\d{2}\.\d{2} \d{2}:\d{2}(:\d{2})?")

cd_lines = []      # (lineno, val)
params_all = []    # (lineno, key, val)
periods_all = []   # (lineno, text)
target_ln = None

with codecs.open(LOG, "r", "utf-16") as f:
    for i, line in enumerate(f, 1):
        if TARGET in line and target_ln is None:
            target_ln = i
        cm = cd_re.search(line)
        if cm:
            cd_lines.append((i, cm.group(1)))
        pm = period_re.search(line)
        if pm:
            periods_all.append((i, pm.group(0)))
        pr = param_re.search(line)
        if pr:
            params_all.append((i, pr.group(1), pr.group(2)))

print("TARGET 行号:", target_ln)
print("全部 period 行(前25):")
for ln, t in periods_all[:25]:
    print(f"   L{ln}: {t}")

# 用最近的 cd 行界定运行边界
cd_before = [ln for ln, v in cd_lines if ln <= target_ln]
cd_start = max(cd_before) if cd_before else None
cd_after = [ln for ln, v in cd_lines if ln > target_ln]
cd_end = min(cd_after) if cd_after else 10 ** 12

print(f"\nTARGET 落在运行区间: cd_start=L{cd_start} (cooldown={dict(cd_lines).get(cd_start)})  ->  cd_end=L{cd_end}")
print("该运行内的 period 行:")
for ln, t in periods_all:
    if (cd_start or 0) <= ln <= cd_end:
        print(f"   L{ln}: {t}")

print(f"\n===== 对拍对象(用户 2025.06.01~2026.08.29 运行)完整参数 =====")
for ln, k, v in params_all:
    if (cd_start or 0) < ln < cd_end:
        print(f"   L{ln}: {k} = {v}")

keys = {k for ln, k, v in params_all if (cd_start or 0) < ln < cd_end}
print("\n=> InpRevStrongOnly 存在:", "InpRevStrongOnly" in keys, "值:",
      dict((k, v) for ln, k, v in params_all if k == "InpRevStrongOnly" and (cd_start or 0) < ln < cd_end))
print("=> InpUseReversalFilter 存在:", "InpUseReversalFilter" in keys)
print("=> InpRsiOverbought 值:",
      [v for ln, k, v in params_all if k == "InpRsiOverbought" and (cd_start or 0) < ln < cd_end])
