# -*- coding: utf-8 -*-
"""
bootstrap.py — 会话启动总入口（astra 最终方案，2026-09-13）

每次会话启动（或发现对话被压缩后）执行一次，把"主动读记忆"变成确定性动作。

它做的事：
  1. 读全局规则 + ~/.workbuddy/MEMORY.md 头部的硬规则
  2. 读中枢的 active_tasks / facts / decisions / incidents 摘要
  3. 打印一个精简的「本轮记忆快照」，供 DeepSeek 直接注入上下文
  4. 报告中枢状态（条目数、最近更新、待审批 inbox 数）

用法：
  python bootstrap.py            # 输出记忆快照
  python bootstrap.py --full     # 输出完整（含更多细节）
"""
import os
import json
import time

HUB = os.path.dirname(os.path.abspath(__file__))
WORKBUDDY_MEM = os.path.expanduser(r'~\.workbuddy\MEMORY.md')
FULL = '--full' in __import__('sys').argv


def read(path, limit=None):
    try:
        with open(path, encoding='utf-8') as f:
            c = f.read()
        return c[:limit] if limit else c
    except Exception:
        return ''


def sink_stats():
    try:
        d = json.load(open(os.path.join(HUB, 'sink.json'), encoding='utf-8'))
        total = sum(len(v) for v in d.values() if isinstance(v, list))
        return total
    except Exception:
        return 0


def inbox_pending():
    p = os.path.join(HUB, 'inbox', 'inbox.jsonl')
    if not os.path.exists(p):
        return 0
    n = 0
    for line in open(p, encoding='utf-8'):
        try:
            if json.loads(line).get('status') == 'pending':
                n += 1
        except Exception:
            pass
    return n


def main():
    out = []
    out.append('================ 记忆快照（bootstrap，%s） ================' % time.strftime('%H:%M:%S'))

    # 1. 硬规则（MEMORY.md 头部，最关键，置顶）
    mem = read(WORKBUDDY_MEM)
    # 提取"硬规则"和"提及禁忌"段
    rules = []
    for kw in ('硬规则', '提及禁忌', '每次回答前的强制动作'):
        idx = mem.find(kw)
        if idx >= 0:
            seg = mem[idx:idx + 900]
            rules.append(seg)
    if rules:
        out.append('【硬规则/禁忌（务必遵守）】')
        out.append('\n'.join(rules[:3]))

    # 2. 当前任务
    at = read(os.path.join(HUB, 'active_tasks.md'), 1200)
    if at:
        out.append('\n【当前任务】')
        out.append(at)

    # 3. 永久事实（精简）
    facts = read(os.path.join(HUB, 'facts.md'), 1000 if not FULL else 3000)
    if facts:
        out.append('\n【永久事实】')
        out.append(facts)

    # 4. 关键结论
    dec = read(os.path.join(HUB, 'decisions.md'), 800 if not FULL else 2500)
    if dec:
        out.append('\n【关键结论】')
        out.append(dec)

    # 5. 状态
    out.append('\n【中枢状态】')
    out.append('sink 条目数: %d | inbox 待审批: %d | 文档索引: docs/doc_index.jsonl' % (
        sink_stats(), inbox_pending()))

    out.append('\n【本轮行动提示】')
    out.append('回答涉及历史事实前，先跑 preflight.py "<用户原话>" 检索；回答后跑 post_turn.py 沉淀。')

    out.append('==============================================================')
    print('\n'.join(out))


if __name__ == '__main__':
    main()
