# -*- coding: utf-8 -*-
"""
post_turn.py — 每轮对话末尾反思（astra 方案，2026-09-13）

在每轮回答结束后调用，判断本轮是否产生了值得沉淀进记忆中枢的内容。
规则初筛（不调模型，零成本）→ 候选写入 inbox/inbox.jsonl → 由批处理统一复核入库。

用法：
  python post_turn.py --user "用户这轮说的话" --assistant "我的回答摘要" [--session SID]

规则初筛（只收这几类，其余当噪声丢弃）：
  1. 用户明确的偏好/硬规则/禁忌（"以后要…/以后不要…/永远…/禁止…"）
  2. 已确认的决策/结论（"就用X/改成X/定了"）
  3. 长期事实（身份、账号、路径、环境）
  4. 未完成任务 + 下一步
  5. 事故与修复（血泪教训）
  6. 稳定工作方法/流程
"""
import os
import sys
import json
import time
import hashlib
import argparse

HUB = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(HUB, 'inbox', 'inbox.jsonl')

# 触发沉淀的强信号（用户原话里的关键词）
STRONG_SIGNALS = [
    '以后', '以后要', '以后不要', '永远', '禁止', '记住', '记住这个', '必须',
    '就用', '改成', '定了', '确定了', '结论', '下次', '从今', '不许', '别在',
    '血泪', '教训', '踩坑', '修复', '根因', '方案',
]

# 弱信号（需配合内容判断）
WEAK_SIGNALS = ['路径', '账号', '密码', '环境', '工具', '脚本', '命令']


def norm(s):
    """规范化文本，用于去重"""
    s = s.lower().strip()
    for ch in ' \t\n\r，。、！？；：""''（）【】《》':
        s = s.replace(ch, '')
    return s


def extract_candidate(user, assistant):
    """从本轮内容提取候选沉淀条目。返回 [(type, text)] 或 []"""
    cands = []
    u = user or ''
    a = assistant or ''

    # 类型1：用户硬规则/偏好/禁忌
    if any(k in u for k in ['以后', '永远', '禁止', '记住', '必须', '不许', '别在', '从今']):
        cands.append(('fact', '用户规则/偏好: ' + u.strip()[:400]))

    # 类型2：已确认决策/结论
    if any(k in u for k in ['就用', '改成', '定了', '确定了', '结论', '方案']):
        cands.append(('decision', '决策: ' + u.strip()[:400]))

    # 类型3：事故与修复
    if any(k in (u + a) for k in ['根因', '修复', '血泪', '教训', '踩坑', '回滚', '崩溃']):
        # 只收结论性的（含"根因""修复"且较长）
        snippet = (u + ' | ' + a).strip()
        if len(snippet) > 30:
            cands.append(('incident', '事故/修复: ' + snippet[:500]))

    # 类型4：未完成任务 + 下一步（含"待""遗留""下一步"）
    if any(k in (u + a) for k in ['待办', '遗留', '下一步', '待用户', '待确认', '阻塞']):
        cands.append(('todo', '待办: ' + (u + ' | ' + a).strip()[:400]))

    return cands


def dedupe_exists(text):
    """检查 inbox + sink.json 是否已有高度相似条目"""
    n = norm(text)
    if not n:
        return False
    # 查 inbox
    if os.path.exists(INBOX):
        for line in open(INBOX, encoding='utf-8'):
            try:
                e = json.loads(line)
            except Exception:
                continue
            if norm(e.get('text', ''))[:60] == n[:60]:
                return True
    # 查 sink.json
    sink = os.path.join(HUB, 'sink.json')
    if os.path.exists(sink):
        try:
            d = json.load(open(sink, encoding='utf-8'))
            for t in ('fact', 'decision', 'incident', 'todo', 'experience'):
                for e in d.get(t, []) if isinstance(d.get(t), list) else []:
                    if norm(str(e.get('text', '')))[:60] == n[:60]:
                        return True
        except Exception:
            pass
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--user', default='')
    ap.add_argument('--assistant', default='')
    ap.add_argument('--session', default='default')
    args = ap.parse_args()

    cands = extract_candidate(args.user, args.assistant)
    if not cands:
        print('[post_turn] 无候选（本轮无需沉淀）')
        return

    os.makedirs(os.path.dirname(INBOX), exist_ok=True)
    added = 0
    with open(INBOX, 'a', encoding='utf-8') as f:
        for t, text in cands:
            if dedupe_exists(text):
                print('[post_turn] 跳过（重复）: %s' % text[:50])
                continue
            rec = {
                'type': t,
                'text': text,
                'source': 'post_turn',
                'session': args.session,
                'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
                'hash': hashlib.md5(norm(text).encode()).hexdigest()[:12],
                'status': 'pending',  # pending -> approved(入库) / dropped(丢弃)
            }
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            added += 1
            print('[post_turn] 候选入 inbox: [%s] %s' % (t, text[:60]))

    print('[post_turn] 本轮共 %d 条候选待复核' % added)


if __name__ == '__main__':
    main()
