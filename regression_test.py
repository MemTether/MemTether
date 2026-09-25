# -*- coding: utf-8 -*-
"""8 条真实查询的回归测试（astra 建议的验收标准）"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import memtether_paths as _mp
    _db = _mp.default_db()
    if _db and os.path.isfile(_db):
        os.environ.setdefault('MEM_DB', _db)
        _store = os.path.join(os.path.dirname(_db), 'mem0_store')
        if os.path.isdir(_store):
            os.environ.setdefault('MEM_STORE', _store)
except ImportError:
    pass
import memsearch

CASES = [
    ('豆包数据在哪', ['Doubao', '豆包']),
    ('怎么迁移数据', ['迁移', 'robocopy', 'Junction']),
    ('smart_audit是什么', ['smart_audit', '审核']),
    ('三大机制', ['机制', '会谈', '审核', 'Tier']),
    ('grok是谁', ['grok', 'Grok', '龙虾', 'OpenClaw']),
    ('生图', ['Fooocus', 'ComfyUI', '生图']),
    ('STM32', ['STM32']),
    ('APK', ['APK']),
]

passed = 0
print('%-22s | %-6s | top1' % ('查询', '结果'))
print('-' * 95)
for q, expects in CASES:
    r = memsearch.search_hybrid(q, limit=3)
    top1 = r['results'][0] if r['results'] else None
    if not top1:
        print('%-22s | %-6s | (无结果)' % (q, 'FAIL'))
        continue
    hit = any(e.lower() in top1['content'].lower() for e in expects)
    if hit:
        passed += 1
    print('%-22s | %-6s | %.3f %s' % (q, 'PASS' if hit else 'FAIL', top1['score'], top1['content'][:48]))
print('-' * 95)
print('通过: %d/%d' % (passed, len(CASES)))
