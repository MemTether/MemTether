# -*- coding: utf-8 -*-
"""pair_superseded.py —— 为「无替代者」的 superseded 找替代关系（候选生成 + 人工确认写入）

【背景】2026-09-16 迁移 bi-temporal 时发现：41 条 superseded 里 24 条 superseded_by 为空。
  逐条读内容后判断它们**不是随机丢失**，而是**导入去重/过程记录换代**的产物：
    legacy 批 08:51:23–29（旧记忆导出）与 workbuddy 批 08:51:34–38（新一轮导入）
    之间存在多组"同一事实的前后版本"。
  即替代关系**客观存在**，只是导入脚本没落库。

【为什么要两种相似度一起看】
  字面（difflib）能精确捕捉"仅个别字改动"的重复：
      08:51:34 "…豆包设置入口在左下角头像，消息通知仅管应用内消息。"  L=61
      08:51:37 "…豆包设置在左下角头像，消息通知仅管应用内消息。"      L=59   → 字面 0.99
  语义（bge-m3 余弦）才能捕捉"叙述过程 → 陈述结论"的重写：
      过程 "通知问题定案(2026-09-11): 系统Toast+豆包AUMID通道均实测可用…"
      结论 "豆包没有任务完成系统通知开关，需用notify_bridge.py借豆包AUMID弹Toast…"
      字面仅 0.41（会漏），语义 0.91（能抓到）。
  ★但**两种分数都不能直接采信**——本库教训（SKILL.md 坑 15）：规则法在自然语言关系
    判定上的天花板就是「人工复核候选生成器」。实测反例：5579d5→ac4952 语义 0.842、
    gap 0.049 过了自动阈值，但两条讲的完全不是一件事（一条讲 notify 开关、一条讲
    席位降级链），属误配。所以 **HIGH 档也必须逐条 eyeball**，确认后才写。

【写入纪律】
  只有 CONFIRMED 清单里的人工确认对才写库，标 temporal_source='inferred'（可追溯为推断）。
  其余一律留空并在 docs/supersede_candidates.json 留档，供后续人工处理。

用法：
  python pair_superseded.py              生成报告（只读）
  python pair_superseded.py --dump       另存候选 JSON
  python pair_superseded.py --apply      写入 CONFIRMED 清单
"""
import difflib
import json
import os
import sqlite3
import sys

import numpy as np

HUB = r'<HUB>'
DB = os.path.join(HUB, 'memory.db')
CAND = os.path.join(HUB, 'docs', 'supersede_candidates.json')

SEM_MIN, GAP_MIN = 0.72, 0.04

# ---- 人工复核确认的替代关系（2026-09-16 eyeball 过）----
# 判定依据：同主题 + 后版是前版的精炼/定稿 + 语义 ≥0.90 + 时间更晚
CONFIRMED = [
    ('fact-20260913085134-5cae46a593', 'fact-20260913085137-5579d5d2f9', 0.987,
     '同一句仅改「入口」二字，后版为定稿'),
    ('fact-20260913085134-36f52ff197', 'fact-20260913085137-1f65213c32', 0.985,
     '预算表述同一内容，后版语序整理'),
    ('fact-20260915162121-2cbe3e3a23', 'fact-20260915162212-221c0e2808', 0.975,
     '「破除4000天花板」同题，后版为 active 定稿'),
    ('fact-20260913085134-49bd002b05', 'fact-20260913085137-c9c2c30a4d', 0.906,
     'plan_council 主席链同一事实，后版补全输出项'),
    ('fact-20260913085134-e56c7d4f49', 'fact-20260913085137-a2ffdb65a0', 0.903,
     'plan_council 强制前置同一规定，后版补全席位名'),
    ('fact-20260913085125-29deea276a', 'fact-20260913085128-8647614e6f', 0.923,
     'consilium.py opener.open() 修复，前版为根因、后版为修复状态汇总'),
    ('fact-20260913085125-516e54d39f', 'fact-20260913085128-2525536375', 0.912,
     '第三大机制 plan_council，前版为落地记录、后版为版本快照'),
]

# ---- 明确排除的自动候选（记录在案，避免后人重复踩）----
REJECTED = [
    ('fact-20260913085137-5579d5d2f9', 'fact-20260913085138-ac4952343d', 0.842,
     '语义过阈但主题不同：前讲 notify 开关、后讲席位降级链 → 误配，剔除'),
]


def load():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def build_pairs():
    """返回 (孤儿列表, 候选池, 每条孤儿的 top3)。"""
    c = load()
    orphans = c.execute(
        "SELECT uid,content,created_at,source FROM facts WHERE status='superseded' "
        "AND (superseded_by IS NULL OR superseded_by='') ORDER BY created_at").fetchall()
    pool = c.execute(
        "SELECT uid,status,content,created_at,source FROM facts "
        "WHERE content IS NOT NULL AND content!=''").fetchall()
    c.close()

    try:
        import memsearch
        texts = [p['content'] for p in pool]
        V = np.array(memsearch._embed(texts), dtype=np.float32)
        V = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
        idx = {p['uid']: i for i, p in enumerate(pool)}
        sem_ok = True
    except Exception as e:
        print('  (语义路不可用: %s，退回字面)' % str(e)[:60])
        sem_ok = False

    out = []
    for o in orphans:
        scored = []
        for p in pool:
            if p['uid'] == o['uid']:
                continue
            if (p['created_at'] or '') <= (o['created_at'] or ''):
                continue          # 替代者必须更晚
            lit = difflib.SequenceMatcher(None, o['content'], p['content']).ratio()
            sem = float(V[idx[o['uid']]] @ V[idx[p['uid']]]) if sem_ok else 0.0
            scored.append((max(lit, sem), lit, sem, p))
        scored.sort(key=lambda x: -x[0])
        top = scored[:3]
        best = top[0]
        gap = best[0] - (top[1][0] if len(top) > 1 else 0.0)
        out.append({'o': o, 'best': best[3], 'lit': best[1], 'sem': best[2],
                    'score': best[0], 'gap': gap, 'top': top})
    out.sort(key=lambda x: -x['score'])
    return out


def report(pairs):
    print('=' * 104)
    print('superseded 替代者候选  |  孤儿 %d 条' % len(pairs))
    print('=' * 104)
    auto = 0
    for r in pairs:
        s = r['score']
        flag = 'HIGH' if (s >= SEM_MIN and r['gap'] >= GAP_MIN) else ('mid ' if s >= 0.62 else 'low ')
        if flag == 'HIGH':
            auto += 1
        print('[%s] 综合 %.3f (字面 %.3f / 语义 %.3f) gap=%.3f  %s'
              % (flag, s, r['lit'], r['sem'], r['gap'], r['o']['uid'][:30]))
        print('   旧: %s' % r['o']['content'][:80].replace('\n', ' '))
        print('   新: %s' % r['best']['content'][:80].replace('\n', ' '))
        print('       -> %s [%s]' % (r['best']['uid'][:30], r['best']['status']))
        print()
    print('=' * 104)
    print('自动过阈值 %d 条 —— 但**均须人工复核**（实测存在过阈误配，见脚本头注释）' % auto)
    print('人工确认可写入 %d 条；明确排除 %d 条。' % (len(CONFIRMED), len(REJECTED)))


def dump(pairs):
    os.makedirs(os.path.dirname(CAND), exist_ok=True)
    data = []
    for r in pairs:
        data.append({
            'orphan': r['o']['uid'],
            'orphan_preview': r['o']['content'][:120],
            'best': r['best']['uid'], 'best_status': r['best']['status'],
            'best_preview': r['best']['content'][:120],
            'score': round(r['score'], 4), 'literal': round(r['lit'], 4),
            'semantic': round(r['sem'], 4), 'gap': round(r['gap'], 4),
            'confirmed': any(r['o']['uid'] == a for a, _, _, _ in CONFIRMED),
        })
    payload = {'generated_at': '2026-09-16', 'orphans': len(pairs),
               'confirmed': [{'old': a, 'new': b, 'sem': s, 'why': w} for a, b, s, w in CONFIRMED],
               'rejected': [{'old': a, 'new': b, 'sem': s, 'why': w} for a, b, s, w in REJECTED],
               'candidates': data}
    with open(CAND, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print('候选已留档 → %s' % CAND)


def _resolve(c, uid_or_prefix):
    """按完整 uid 或前缀解析出唯一记录（报告里的 uid 是截断显示，这里容错）。"""
    r = c.execute('SELECT uid,status,created_at FROM facts WHERE uid=?', (uid_or_prefix,)).fetchone()
    if r:
        return r
    rs = c.execute('SELECT uid,status,created_at FROM facts WHERE uid LIKE ?',
                   (uid_or_prefix + '%',)).fetchall()
    if len(rs) == 1:
        return rs[0]
    if len(rs) > 1:
        print('  ! 前缀 %s 命中 %d 条，需给全 uid' % (uid_or_prefix, len(rs)))
    return None


def apply_confirmed():
    c = load()
    n = 0
    for old, new, sem, why in CONFIRMED:
        t = _resolve(c, new)
        o = _resolve(c, old)
        if not t or not o:
            print('  ✗ 未解析到: %s → %s' % (old[:30], new[:30]))
            continue
        c.execute("UPDATE facts SET superseded_by=?, valid_to=?, invalidated_at=?, "
                  "temporal_source='inferred' WHERE uid=?",
                  (t['uid'], t['created_at'], t['created_at'], o['uid']))
        n += 1
        print('  ✓ %s → %s' % (o['uid'][:30], t['uid'][:30]))
    c.execute("UPDATE facts SET invalidated_at=valid_to WHERE status='superseded' "
              "AND valid_to IS NOT NULL AND valid_to!='' "
              "AND (invalidated_at IS NULL OR invalidated_at='')")
    c.commit()
    print('已写入 %d 条替代链（temporal_source=inferred，可追溯为推断）' % n)
    c.close()


if __name__ == '__main__':
    ps = build_pairs()
    report(ps)
    if '--dump' in sys.argv:
        dump(ps)
    if '--apply' in sys.argv:
        print()
        apply_confirmed()
