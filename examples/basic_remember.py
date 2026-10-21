"""examples/basic_remember.py — 10 行体验 MemTether 核心读写

运行: python examples/basic_remember.py
(需要先 pip install memtether 或在本仓根目录 python 直接跑)
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolate this demo into its own throwaway DB.
# Without this, a machine that has local_paths.json pointing at a real hub would
# have the example writing "source=demo" rows into the production memory.db
# (2026-10-21 audit: it really did, and then crashed on the older schema).
_demo_db = os.path.join(tempfile.mkdtemp(prefix='memtether_demo_'), 'demo.db')
os.environ['MEM_DB'] = _demo_db
os.environ['MEM_SKIP_VECTOR'] = '1'

from gateway import remember, search, correct

# 1. 写一条记忆
r = remember("用户的项目用 MemTether 管理跨客户端记忆", source="demo")
print("写入:", r["uid"], r["op"])

# 2. 检索
res = search("跨客户端记忆", limit=3)
print("检索到", len(res.get("results", [])), "条")
for x in res.get("results", [])[:3]:
    print("  -", (x.get("content") or "")[:60], "| source:", x.get("source"))

# 3. 纠正（supersession：旧值不删）
r2 = correct(r["uid"], "用户的项目已用 MemTether v2 管理跨客户端记忆", reason="版本更新",
             by_agent="demo")
print("纠正:", r2.get("new_uid"))