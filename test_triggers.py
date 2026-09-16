# -*- coding: utf-8 -*-
"""测试 incident / on_miss 触发器"""
import sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway

print('=== incident 触发器（出错即时记录）===')
r = gateway.incident(
    step='创建 Junction 目录联接',
    error='cmd.exe 被安全策略拦截，mklink 不可用',
    workaround='改用 New-Item -ItemType Junction',
    result='成功',
    )
print(json.dumps(r, ensure_ascii=False))

print()
print('=== on_miss 触发器（答不上的查询 -> 应记录）===')
r2 = gateway.on_miss('量子纠缠退相干机制是什么')
print(json.dumps(r2, ensure_ascii=False))

print()
print('=== on_miss 触发器（答得上的查询 -> 应跳过）===')
r3 = gateway.on_miss('生图')
print(json.dumps(r3, ensure_ascii=False))

print()
print('=== 待处理事件队列 ===')
import sqlite3
conn = sqlite3.connect(gateway.DB)
conn.row_factory = sqlite3.Row
for row in conn.execute("SELECT run_id, event_type, agent, status FROM run_events ORDER BY id").fetchall():
    print('  [%s] %s | %s | %s' % (row['event_type'], row['run_id'][:40], row['agent'], row['status']))
conn.close()
