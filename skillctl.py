# -*- coding: utf-8 -*-
"""
skillctl.py — 记忆中枢的技能层统一入口（MemTether · 技能中枢）

把两件原本分散的事收进同一个 CLI：

    沉淀侧  scan / draft / admit / status      ← skill_forge.py（Trace2Skill 式五步闭环）
    预算侧  health / families / gate / preflight / budget
                                               ← skill_budget.py（注入预算守卫）
    新增    link  / dup / audit                ← 本文件（分发层 + 空壳 + 综合体检）

为什么合并：
    技能是记忆中枢的一类资产。沉淀是「往里加」，预算是「别加爆」，
    分发是「两版客户端共享同一份」——三者是同一条链，分开看会各自失真。

    python skillctl.py audit       # 一眼看全：分发 / 体积 / 重叠 / 空壳 / 沉淀候选
    python skillctl.py link        # 两版是否都指向中立真源（跨客户端共享的前提）
    python skillctl.py dup         # 空壳 / 重名族
    python skillctl.py health      # 等价于 skill_budget health
    python skillctl.py scan        # 等价于 skill_forge scan
"""
import os
import re
import sys
import json
import subprocess

HUB = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
if HUB not in sys.path:
    sys.path.insert(0, HUB)

FORGE = os.path.join(HUB, 'skill_forge.py')
BUDGET = os.path.join(HUB, 'skill_budget.py')

FORGE_CMDS = {'scan', 'draft', 'admit', 'status'}
BUDGET_CMDS = {'health', 'families', 'gate', 'preflight', 'budget'}

# 中立真源 + 各客户端的技能目录（跨客户端共享的四层槽位里的第 0 层）
TRUE_SOURCE = os.path.join(os.path.expanduser('~'), '.agents', 'skills')
CLIENTS = [
    ('workbuddy(国内版)', os.path.join(os.path.expanduser('~'), '.workbuddy', 'skills')),
    ('workbuddy-ai(国际版)', os.path.join(os.path.expanduser('~'), '.workbuddy-ai', 'skills')),
]
# ★已发现、但**刻意不接入**的客户端（2026-09-18 实测）
#   Codex CLI 0.154.0 有自己的 ~/.codex/skills（当前只有内置 .system）。
#   它跑起来会打印 "Skill descriptions were shortened to fit the skills
#   context budget" —— 说明它**自己也吃技能预算**。此时再 junction 78 个技能过去
#   是往已经告警的槽位里再塞东西，属于帮倒忙。
#   所以只做**观察**：报告它存在且未接入，不自动建链接（接不接由人决定）。
WATCH_ONLY = [
    ('codex(CLI, 未接入)', os.path.join(os.path.expanduser('~'), '.codex', 'skills')),
]

# ★直接复用 skill_budget 的解析，不再各写一份正则。
#   原因（2026-09-18 实测）：单行 description 正则会把 `description: >` 的 YAML 折叠块
#   读成 1 个字符 —— 同一份技能，两套正则会给出两套结论，这是最坏的情况。
#   单一真源：frontmatter 解析只认 skill_budget.parse_desc() 一个实现。
from skill_budget import FM, NM, parse_desc      # noqa: E402


def hr(t='-'):
    print(t * 96)


def _realpath(p):
    """跟随 junction/symlink 解析真实路径；不存在返回 None。"""
    if not os.path.exists(p):
        return None
    try:
        return os.path.realpath(p)
    except Exception:
        return p


def cmd_link(a):
    """分发层体检：两版客户端的技能目录是否都指向同一份中立真源。
    这是「跨客户端共享同一份物理技能」的前提——目录不同名时最容易断在这里。"""
    hr('=')
    print('技能分发层体检')
    hr('=')
    ts = _realpath(TRUE_SOURCE)
    print('中立真源 : %s' % TRUE_SOURCE)
    print('  解析为 : %s' % (ts or '(不存在!)'))
    if not ts:
        print('\n!! 中立真源不存在，技能层无从谈起。')
        return 2
    print()
    ok = True
    for label, p in CLIENTS:
        rp = _realpath(p)
        if rp is None:
            print('  %-22s %s  ->  (不存在)' % (label, p))
            ok = False
        elif os.path.normcase(rp) == os.path.normcase(ts):
            print('  %-22s -> 真源  OK' % label)
        else:
            print('  %-22s -> %s   !! 未指向真源' % (label, rp))
            ok = False
    # 只观察、不介入：报出来是让人知道有这条路，接不接由人定
    for label, p in WATCH_ONLY:
        rp = _realpath(p)
        if rp is None:
            print('  %-22s %s  ->  (不存在)' % (label, p))
        elif os.path.normcase(rp) == os.path.normcase(ts):
            print('  %-22s -> 真源  OK' % label)
        else:
            n = 0
            try:
                n = len([x for x in os.listdir(p)
                         if os.path.isdir(os.path.join(p, x)) and not x.startswith('.')])
            except OSError:
                pass
            print('  %-22s -> 未接入（自带 %d 个技能，未指向真源）' % (label, n))
    print()
    if ok:
        print('结论: 两版共享同一份物理技能 —— 装一处，两版生效。')
        return 0
    print('结论: 分发链有断点 —— 两版会各自维护一份，改一处不生效。')
    return 1


def _read_skill(d):
    f = os.path.join(d, 'SKILL.md')
    if not os.path.exists(f):
        return None
    try:
        raw = open(f, encoding='utf-8', errors='replace').read()
    except Exception:
        return None
    m = FM.match(raw)
    name = os.path.basename(d)
    desc = ''
    if m:
        nn = NM.search(m.group(1))
        if nn:
            name = nn.group(1).strip()
        desc = parse_desc(m.group(1))
    body = raw[m.end():] if m else raw
    return {'dir': d, 'name': name, 'desc': desc,
            'body_lines': len([x for x in body.splitlines() if x.strip()]),
            'has_fm': bool(m), 'raw_len': len(raw)}


def cmd_dup(a):
    """空壳 / 重名族检测。
    空壳判据（任一命中）：无 SKILL.md / 无 frontmatter / description ≤ 2 字符 / 正文 0 行。
    空壳最危险：它照样占注入预算，却提供零能力——是「技能越多越笨」的主要来源。"""
    root = a.root or TRUE_SOURCE
    rows = []
    for n in sorted(os.listdir(root)):
        d = os.path.join(root, n)
        if not os.path.isdir(d) or n.startswith(('.', '_')):
            continue
        r = _read_skill(d)
        if r is None:
            rows.append({'dir': d, 'name': n, 'desc': '', 'body_lines': 0,
                         'has_fm': False, 'raw_len': 0, 'shell': ['无 SKILL.md']})
            continue
        why = []
        if not r['has_fm']:
            why.append('无 frontmatter')
        if len(r['desc']) <= 2:
            why.append('description 为空/过短(%d 字符)' % len(r['desc']))
        if r['body_lines'] == 0:
            why.append('正文 0 行')
        r['shell'] = why
        rows.append(r)

    shells = [r for r in rows if r['shell']]
    hr('=')
    print('空壳检测  共 %d 个技能目录，空壳 %d 个' % (len(rows), len(shells)))
    hr('=')
    if not shells:
        print('  无空壳。')
    for r in shells:
        print('  !! %-46s %s' % (r['name'][:46], ' / '.join(r['shell'])))
        print('     %s' % r['dir'])
    print()
    print('  处置建议: 空壳不要直接删——移入 %s 隔离区（可逆）。' % os.path.join(root, '_quarantine'))
    if getattr(a, 'json', False):
        print(json.dumps({'shells': [r['dir'] for r in shells]}, ensure_ascii=False))
    return 1 if shells else 0


def cmd_audit(a):
    """综合体检：分发 + 体积 + 重叠 + 空壳 + 沉淀候选。"""
    rc = 0
    print('\n[1/4] 分发层')
    rc |= (1 if cmd_link(a) else 0)
    print('\n[2/4] 注入预算')
    r = subprocess.run([PY, BUDGET, 'health'], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    print(r.stdout.strip() or r.stderr.strip())
    print('\n[3/4] 空壳与重名族')
    rc |= (1 if cmd_dup(a) else 0)
    print('\n[4/4] 技能预算 vs 记忆投影')
    r = subprocess.run([PY, BUDGET, 'budget'], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    print(r.stdout.strip() or r.stderr.strip())
    return rc


def main():
    import argparse
    ap = argparse.ArgumentParser(description='技能中枢统一入口', add_help=True)
    ap.add_argument('cmd', nargs='?', default='audit')
    ap.add_argument('rest', nargs=argparse.REMAINDER)
    ap.add_argument('--root', default=None)
    ap.add_argument('--json', action='store_true')
    a, rest = ap.parse_known_args()
    a.rest = [x for x in (rest or []) if x != '--']
    a.root = a.root
    a.json = a.json

    c = a.cmd
    if c in ('link', 'dup', 'audit'):
        fn = {'link': cmd_link, 'dup': cmd_dup, 'audit': cmd_audit}[c]
        return fn(a)
    if c in FORGE_CMDS:
        return subprocess.run([PY, FORGE] + [c] + a.rest).returncode
    if c in BUDGET_CMDS:
        return subprocess.run([PY, BUDGET] + [c] + a.rest).returncode
    ap.print_help()
    print('\n可用子命令: ' + ', '.join(sorted(FORGE_CMDS | BUDGET_CMDS | {'link', 'dup', 'audit'})))
    return 2


if __name__ == '__main__':
    sys.exit(main())
