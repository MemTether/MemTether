# -*- coding: utf-8 -*-
"""rerank_k_bench.py —— 精排候选数(rerank_k) 的精度/耗时权衡评测

【为什么留着这个脚本】
  2026-09-16 用 cProfile + 组件包裹定位到：单次查询耗时里 **精排占 98.4%**，
  其余全部环节（sqlite/chromadb/embedding/关键词/RRF）合计只有 0.03s。
  也就是说想调查询速度，**除了精排没有别的杠杆**。这个脚本就是那把尺子。

【实测基线（62 题同一卷子，本机 16 核 CPU / bge-reranker-base int8）】
  rerank_k=30 → 59/62  单题 1.591s   ← 生产默认，精度优先
  rerank_k=20 → 57/62  单题 1.108s   ← 比 k=15 还差，证明 ±2 题属噪声
  rerank_k=15 → 58/62  单题 0.825s
  rerank_k=10 → 56/62  单题 0.685s
  ★结论：降 k 是明确的"拿精度换速度"，不是免费午餐，所以默认值保持 30。
    需要更快时用环境变量 MEM_RERANK_K=15 自行权衡（无需改代码）。

【2026-09-16 修复传参污染后复测（同一卷子 62 题）】
  rerank_k=30 → 59/62  1.675s/题 ／ k=20 → 58/62  1.967s/题
  rerank_k=15 → 58/62  0.840s/题 ／ k=10 → 56/62  0.626s/题
  → **准确率与上表逐档吻合**（k=20 的 57→58 在 ±2 噪声内），确认此前的档位结论可信。
  → 耗时受机器负载影响可达 ±50%（本次 k=20 反而比 k=30 慢，就是负载噪声，
     不是"k 越大越快"这类反直觉规律）；**耗时只能同批对比，别跨批比较**。

【顺带证伪的两条"想当然"（都不是杠杆）】
  ① 固定 padding 到 128：真实记忆条目中位 184 字符、多数已被截断到 128 token，
     固定 pad 等于没 pad —— 0.605s vs 动态 pad 0.599s，且排序 Spearman=1.0 完全一致。
     （这与 embedding 那边"2 token 短句被撑到 512 慢 73 倍"是不同的坑，别混淆。）
  ② onnxruntime 线程数：intra_op=4 反而更慢（0.605→0.668s）。
     小 batch 下线程同步开销大于并行收益，默认配置已是最优。

用法：python rerank_k_bench.py
"""
"""评测：精排候选数 rerank_k 对 62 题准确率与耗时的影响。"""
import os, sys, json, re, time, subprocess
HERE = r'<HUB>'
PY = os.path.join(HERE, '.venv-memory', 'Scripts', 'python.exe')
sys.path.insert(0, HERE)
from hard_bench import CASES, score, cases_ipc

script = r'''
import os, sys, json, time
sys.path.insert(0, %r)
import memsearch
cases = json.load(open(os.environ['HB_CASES'], encoding='utf-8'))
RK = int(os.environ.get('PROBE_RK', '30'))
out, t0 = [], time.perf_counter()
for c in cases:
    r = memsearch.search_hybrid(c['q'], limit=10, rerank_k=RK, decay=False)
    out.append({'id': c['id'], 'blob': [x['content'] for x in r.get('results', [])]})
wall = time.perf_counter() - t0
print(json.dumps({'rk': RK, 'wall': wall, 'rows': out}, ensure_ascii=False))
''' % HERE

_ipc = cases_ipc()
env0 = dict(os.environ); env0['HB_CASES'] = _ipc; env0['PYTHONPATH'] = HERE

print(f'{"rerank_k":>9}{"准确率":>10}{"62题总耗时":>12}{"单题":>9}')
for rk in (30, 20, 15, 10):
    env = dict(env0); env['PROBE_RK'] = str(rk)
    r = subprocess.run([PY, '-c', script], cwd=HERE, env=env,
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(f'{rk:>9}  失败: {(r.stderr or "")[-200:]}'); continue
    d = json.loads(r.stdout.strip().splitlines()[-1])
    res = score(d['rows'])
    ok = sum(1 for _, st, _ in res if st == 'PASS')
    print(f'{rk:>9}{ok:>7}/{len(CASES)}{d["wall"]:>11.1f}s{d["wall"]/len(CASES):>8.3f}s')

try:
    os.remove(_ipc)
except OSError:
    pass
