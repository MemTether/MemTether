"""bench_latency.py — MemTether 核心操作延迟微基准（p50/p95/p99）

产出论文/README 可引用的硬核性能数字。
用法: python _dev/benchmarks/bench_latency.py [--n 200]
"""
import os, sys, json, time, tempfile, argparse, statistics
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ap = argparse.ArgumentParser()
ap.add_argument('--n', type=int, default=200)
ap.add_argument('--k', type=int, default=10)
args = ap.parse_args()

db = os.path.join(tempfile.gettempdir(), 'latency_bench.db')
if os.path.exists(db): os.remove(db)
os.environ['MEM_DB'] = db
os.environ['MEM_SKIP_VECTOR'] = '1'
# Works both from the repo checkout and from a pip install / clean clone:
# resolve the repo root as the parent of the directory this file lives in,
# falling back to the installed package when not running from a checkout.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
if os.path.isfile(os.path.join(_REPO, 'gateway.py')):
    sys.path.insert(0, _REPO)
else:
    sys.path.insert(0, os.path.dirname(_HERE))  # benchmarks/ inside an install
import gateway, memsearch
gateway.init_db()

def bench(fn, n, label):
    lat = []
    for i in range(n):
        t0 = time.perf_counter()
        fn(i)
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    p50 = lat[n//2]; p95 = lat[int(n*0.95)]; p99 = lat[int(n*0.99)]
    print(f'{label:<24} p50={p50:7.2f}ms  p95={p95:7.2f}ms  p99={p99:7.2f}ms  mean={statistics.mean(lat):7.2f}ms')
    return {'p50': round(p50,2), 'p95': round(p95,2), 'p99': round(p99,2), 'mean': round(statistics.mean(lat),2)}

results = {}
# 1. remember (insert)
results['remember'] = bench(lambda i: gateway.remember(f'bench: ServiceM{i%50} 的 attr{i%7} 是 value-{i}（延迟测试）', source=f'bench-{i%4}'), args.n, 'remember (insert)')

# 2. search (entity query — SGM path)
results['search_sgm'] = bench(lambda i: memsearch.search_hybrid(f'ServiceM{i%50} 的 attr{i%7}', limit=args.k), min(args.n, 50), 'search (SGM entity)')

# 3. search (general keyword)
results['search_kw'] = bench(lambda i: memsearch.search_hybrid(f'延迟测试 value-{i}', limit=args.k), min(args.n, 50), 'search (keyword)')

# 4. correct (supersede)
uids = []
c = __import__('sqlite3').connect(db)
for (uid,) in c.execute("SELECT uid FROM facts WHERE content LIKE 'bench%' LIMIT 30").fetchall():
    uids.append(uid)
c.close()
correct_lat = []
for i, uid in enumerate(uids[:20]):
    t0 = time.perf_counter()
    gateway.correct(uid, f'bench corrected: new value {i}', reason='latency bench', by_agent='codex')
    correct_lat.append((time.perf_counter() - t0) * 1000)
correct_lat.sort()
n_c = len(correct_lat)
results['correct'] = {'p50': round(correct_lat[n_c//2],2), 'p95': round(correct_lat[int(n_c*0.95)],2), 'p99': round(correct_lat[-1],2), 'mean': round(statistics.mean(correct_lat),2)}
print(f"{'correct (supersede)':<24} p50={results['correct']['p50']:7.2f}ms  p95={results['correct']['p95']:7.2f}ms  p99={results['correct']['p99']:7.2f}ms  mean={results['correct']['mean']:7.2f}ms")

out = os.path.join(tempfile.gettempdir(), 'latency_results.json')
json.dump(results, open(out, 'w', encoding='utf-8'), indent=1)
print(f'\nresults: {out}')
os.remove(db)