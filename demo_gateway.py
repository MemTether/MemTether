# -*- coding: utf-8 -*-
"""
demo_gateway.py — 记忆中枢深度互联端到端演示（2026-09-13）

演示完整闭环：
  1. 关键词检索（SQLite 权威库）
  2. 工具资产按任务召回（resolve_task）
  3. Mem0 语义检索（自动提取+语义匹配）
  4. supersession 替代链（过时事实不返回）
"""
import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gateway

print('=' * 60)
print('记忆中枢深度互联 — 端到端演示')
print('=' * 60)

print('\n【1】SQLite 权威库统计')
print(json.dumps(gateway.stats(), ensure_ascii=False))

print('\n【2】按任务召回工具配方（解决"反复扫描"痛点）')
for task in ['生图', 'STM32', 'APK', '模拟器']:
    r = gateway.resolve_task(task)
    tools = [t['name'] for t in r.get('preferred_tools', [])]
    print('  任务「%s」→ 工具: %s' % (task, tools if tools else '（无匹配）'))

print('\n【3】关键词检索')
# ★开源版：查询词用中性占位（原为本机用户的真名）。换成你自己的关键词即可。
r = gateway.search('使用者')
for x in r['results']:
    print('  [%s] %s' % (x['kind'], x.get('content') or x.get('name')))

print('\n【4】supersession 检查：过时的"三大机制"不应作为当前事实返回')
r = gateway.search('三大机制')
active = [x for x in r['results'] if x.get('kind') == 'fact']
print('  关键词"三大机制"命中 %d 条 active 事实:' % len(active))
for x in active:
    print('   - %s' % x['content'][:80])

print('\n【5】Mem0 语义检索（理解"近义"而非"精确词"）')
r = gateway.search('使用者会做什么AI相关的？', mem0=True)
for x in r.get('semantic', []):
    print('  [语义 %.3f] %s' % (x['score'], x['content'][:60]))

print('\n' + '=' * 60)
print('演示完成。所有能力已跑通。')
print('=' * 60)
