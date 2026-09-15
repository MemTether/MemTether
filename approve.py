# -*- coding: utf-8 -*-
"""
approve.py — inbox 候选审批入库（astra 方案，2026-09-13）

把 post_turn.py 产生的 inbox 候选，批量审批准入 sink.json（走 mem.py 的正确通道）。

用法：
  python approve.py             # 列出 inbox 候选
  python approve.py --all       # 全部批准入库
  python approve.py --drop ID   # 丢弃某条
"""
import os
import sys
import json
import time
import subprocess

HUB = r'E:\RUANJIAN\memory_hub'
INBOX = os.path.join(HUB, 'inbox', 'inbox.jsonl')
REVIEW = os.path.join(HUB, 'review', 'review.jsonl')


def load_inbox():
    if not os.path.exists(INBOX):
        return []
    out = []
    for i, line in enumerate(open(INBOX, encoding='utf-8')):
        try:
            e = json.loads(line)
            e['_line'] = i
            out.append(e)
        except Exception:
            continue
    return out


def approve(rec):
    """把一条候选写进 sink.json（通过 mem.py add）"""
    t = rec.get('type', 'fact')
    text = rec.get('text', '')
    r = subprocess.run(
        ['python', os.path.join(HUB, 'mem.py'), 'add',
         '--type', t, '--source', 'workbuddy', '--text', text],
        capture_output=True, text=True, encoding='utf-8', errors='ignore',
    )
    return r.returncode == 0, (r.stdout or r.stderr).strip()[:120]


def main():
    inbox = load_inbox()
    if not inbox:
        print('inbox 为空')
        return

    pending = [e for e in inbox if e.get('status') == 'pending']
    print('inbox 待审批 %d 条：' % len(pending))
    for e in pending:
        print('  [%s] %s' % (e.get('type'), e.get('text', '')[:60]))

    if '--all' not in sys.argv and '--drop' not in sys.argv:
        print('\n用法：python approve.py --all（全部入库） 或 --drop <hash>（丢弃某条）')
        return

    if '--drop' in sys.argv:
        i = sys.argv.index('--drop')
        h = sys.argv[i + 1]
        # 标记丢弃
        new_lines = []
        for e in inbox:
            if e.get('hash') == h:
                e['status'] = 'dropped'
                print('丢弃: %s' % e.get('text', '')[:50])
            new_lines.append(e)
        with open(INBOX, 'w', encoding='utf-8') as f:
            for e in new_lines:
                f.write(json.dumps(e, ensure_ascii=False) + '\n')
        return

    # --all
    ok = 0
    for e in pending:
        succ, msg = approve(e)
        if succ:
            e['status'] = 'approved'
            ok += 1
            print('入库 OK: %s' % e.get('text', '')[:50])
        else:
            e['status'] = 'failed'
            print('入库失败: %s -> %s' % (e.get('text', '')[:40], msg))

    # 回写 inbox
    with open(INBOX, 'w', encoding='utf-8') as f:
        for e in inbox:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')

    print('\n共批准 %d 条入库' % ok)


if __name__ == '__main__':
    main()
