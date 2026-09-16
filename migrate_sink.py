# -*- coding: utf-8 -*-
"""
migrate_sink.py — 把旧 sink.json 迁移到新 SQLite 权威库（阶段3）

策略（astra 裁决）：
  - fact/decision/incident 类型 → 直接迁移为 active fact（保留 source/ts）
  - experience 类型 → 迁移为 fact（type=experience，status=active，除非已过时）
  - todo 类型 → 迁移为 fact（type=todo）
  - 已退役机制相关内容 → 标记为 superseded（由新的"三大机制已退役"决策替代）

输出 migration_report.json（active/candidate/superseded/skipped 统计）。

用法：
  python migrate_sink.py
"""
import os
import json
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway

HUB = r'<HUB>'
SINK = os.path.join(HUB, 'sink.json')

# 已退役机制关键词（这些旧条目应被 supersede，不再当 active 事实）
RETIRED_PATTERNS = [
    'smart_audit_v2', 'plan_council', 'consilium', '三大机制', '审核机制', '方案前置',
    '多方会谈', '主席链', 'roundtable',
]


def load_sink():
    with open(SINK, encoding='utf-8') as f:
        return json.load(f)


def classify(entry):
    """返回 (type, status, content)"""
    text = entry.get('text', '')
    tag = entry.get('tag', '')
    ts = entry.get('ts', '')
    src = entry.get('source', entry.get('sources', ['legacy'])[0] if entry.get('sources') else 'legacy')
    if isinstance(src, list):
        src = src[0] if src else 'legacy'

    # 判断是否退役相关
    if any(k in text for k in RETIRED_PATTERNS):
        # 若包含"退役/削减/仅保留"说明是新状态，保留 active；否则旧机制描述 → superseded
        if any(k in text for k in ['已退役', '退役', '削减', '仅保留', '收敛', '取代']):
            return 'decision', 'active', text
        return 'fact', 'superseded', text

    # 类型映射
    t = entry.get('type', 'fact')
    if t in ('fact', 'decision', 'incident', 'experience', 'todo'):
        return t, 'active', text
    return 'fact', 'active', text


def main():
    gateway.init_db()
    data = load_sink()
    entries = []
    # sink.json 结构：dict of lists
    if isinstance(data, dict):
        for t, lst in data.items():
            if isinstance(lst, list):
                for e in lst:
                    if isinstance(e, dict) and e.get('text'):
                        e = dict(e)
                        e.setdefault('type', t if t != 'experience' else 'experience')
                        entries.append(e)

    report = {'total': len(entries), 'active': 0, 'superseded': 0, 'skipped': 0,
              'candidate': 0, 'migrated': []}

    for e in entries:
        text = e.get('text', '')
        if not text or len(text) < 2:
            report['skipped'] += 1
            continue
        t, status, content = classify(e)
        src = e.get('source', 'legacy')
        if isinstance(src, list):
            src = src[0] if src else 'legacy'
        if src not in ('workbuddy', 'openclaw', 'doubao_a', 'doubao_b', 'user', 'legacy'):
            src = 'legacy'
        ts = e.get('ts', '')
        r = gateway.remember(
            content, type=t, source=src, scope='shared', subject='system',
            confidence=0.7, tags=e.get('tag', ''), status=status
        )
        report['active' if status == 'active' else 'superseded'] += 1
        report['migrated'].append({'uid': r.get('uid'), 'status': status, 'src': src, 'ts': ts, 'text': content[:60]})

    # 写报告
    out = os.path.join(HUB, 'migration_report.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print('=== 迁移完成 ===')
    print('总条目: %d' % report['total'])
    print('active: %d / superseded: %d / skipped: %d / candidate: %d' %
          (report['active'], report['superseded'], report['skipped'], report['candidate']))
    print('报告: %s' % out)
    print()
    print('=== 当前 SQLite 状态 ===')
    print(json.dumps(gateway.stats(), ensure_ascii=False))


if __name__ == '__main__':
    main()
