# -*- coding: utf-8 -*-
"""测试 remember 自动同步向量索引"""
import sys, json
sys.path.insert(0, r'E:\RUANJIAN\memory_hub')
import gateway

# 写一条真实结论（本次工作的成果）
r = gateway.remember(
    'memory_hub 唯一真源已收敛为 memory.db：mem.py 与 preflight.py 均已委托 gateway，'
    '不再各自写 sink.json；检索统一走 memsearch 混合检索（向量+ASCII+字面）。',
    type='fact', source='workbuddy')
print('写入结果:', json.dumps(r, ensure_ascii=False))

# 不手动 rebuild，直接检索
s = gateway.search('唯一真源是哪个')
print('\n检索"唯一真源是哪个":')
for item in s['results'][:3]:
    print('  %.3f [%s] %s' % (item['score'], ','.join(item['reason']), item['content'][:70]))
