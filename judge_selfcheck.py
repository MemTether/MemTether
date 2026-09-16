# -*- coding: utf-8 -*-
"""judge_selfcheck.py —— 评测器自检：把 gold 当预测喂给 judge，看通过率

【为什么做】LongMemEval 60 题结果里 `llm 45.8% < strict 58.5%`，上界低于下界。
  按本库铁律"分数异常先怀疑评测器"，必须先排除 judge 本身有问题的可能。
  判据：若 judge 连 gold 都判错 → 是 judge 的问题，llm 分不可信；
        若 judge 对 gold 通过率接近 100% → 说明 llm 45.8% 是**真实**的端到端能力。

★使用纪律：**报 llm 分之前先跑它**（约 1 分钟，需外部 judge 通道可用）。
  实测 2026-09-16：20 题抽样 18/18 通过 → 判 judge 可信。
用法：python judge_selfcheck.py
"""
import json
import os
import sys

sys.path.insert(0, r'<HUB>')
import bench_longmemeval as B

path = os.path.join(B.DATA, 'longmemeval_oracle')
items = json.load(open(path, encoding='utf-8'))
todo = B.stratified_sample(items, 20)

print('评测器自检：把 **gold 答案本身** 当作预测喂给 judge')
print('（通过率低 → judge 有问题；接近 100% → judge 可信，llm 分是真实的）')
print()

ok_n, tot, fails = 0, 0, []
for it in todo:
    g = it.get('answer')
    if not g:
        continue
    ok, note = B.judge_llm(it['question'], g, g)
    if ok is None:
        print('  [skip] %s' % str(note)[:70])
        continue
    tot += 1
    if ok:
        ok_n += 1
    else:
        fails.append((it['question_type'], it['question'][:70], str(g)[:70], str(note)[:90]))
    print('  [%s] %-26s %s' % ('✓' if ok else '✗', it['question_type'],
                               it['question'][:56]))

print()
print('=' * 74)
print('gold 自判通过率: %d/%d = %.1f%%' % (ok_n, tot, 100.0 * ok_n / tot if tot else 0))
if ok_n == tot:
    print('→ judge 可信：它对标准答案全判对，因此 llm 45.8% 反映的是**真实的端到端能力**')
else:
    print('→ ★judge 有问题：连 gold 都判错 %d 题，llm 分不可采信' % (tot - ok_n))
print()
for qtype, q, g, note in fails[:6]:
    print('  失败样例 [%s]' % qtype)
    print('    Q: %s' % q)
    print('    gold: %s' % g)
    print('    judge 说: %s' % note)
