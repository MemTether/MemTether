# -*- coding: utf-8 -*-
"""把 reflector 来源的 fact 同步到 Mem0 语义索引（补齐语义检索召回）"""
import sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway, sqlite3

conn = sqlite3.connect(gateway.DB)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT content FROM facts WHERE source='reflector' AND status='active'").fetchall()
conn.close()

print('reflector 来源的 active 事实 %d 条，同步到 Mem0...' % len(rows))
for r in rows:
    res = gateway._mem0_add(r['content'])
    ok = 'ok' if not isinstance(res, dict) or 'error' not in res else ('err:' + str(res.get('error',''))[:40])
    print('  [%s] %s' % (ok, r['content'][:50]))
