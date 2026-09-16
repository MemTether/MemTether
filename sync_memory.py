# -*- coding: utf-8 -*-
"""
sync_memory.py — 核心记忆同步（astra 终版，2026-09-13 全量覆盖版）

作用：把 memory_hub 的"永久事实/关键结论"提炼成精简投影，**全量覆盖**同步到
      ~/.workbuddy/MEMORY.md，使 WorkBuddy 侧记忆成为中枢的"启动适配层"，
      不再是独立记忆库（唯一真源 = memory_hub/sink.json）。

与旧版区别：
  - 旧版：追加标记，导致 MEMORY.md 内容重复、拼接错乱。
  - 新版：全量覆盖（原子写），每次生成一份干净、结构固定的 MEMORY.md。

机制：
  1. 读 memory_hub 的 facts.md / decisions.md / incidents.md / active_tasks.md
  2. 与 profile.md（用户画像/硬规则）合并
  3. 生成精简投影，写到 memory_hub/projections/agent_workbuddy.md
  4. 原子覆盖 ~/.workbuddy/MEMORY.md

用法：
  python sync_memory.py            # 同步一次（覆盖）
  python sync_memory.py --check    # 只看差异，不写
"""
import os
import sys
import time

HUB = os.path.dirname(os.path.abspath(__file__))
WORKBUDDY_MEM = os.path.expanduser(r'~\.workbuddy\MEMORY.md')
PROJECTION = os.path.join(HUB, 'projections', 'agent_workbuddy.md')

# 中枢各文件（缺失则跳过）
SECTION_FILES = [
    ('facts.md', '## 永久事实'),
    ('decisions.md', '## 关键结论'),
    ('incidents.md', '## 问题诊断'),
    ('active_tasks.md', '## 当前任务'),
]


def read_if_exists(path):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    return ''


def build_projection():
    parts = []
    parts.append('# MEMORY.md — WorkBuddy 侧记忆（启动适配层）\n')
    parts.append('<!-- 真源：<HUB>（唯一事实源） -->\n')
    parts.append('<!-- 本文件由 sync_memory.py 全量生成，勿手改；改记忆走 mem.py add -->\n')
    parts.append('<!-- 生成时间 %s -->\n' % time.strftime('%Y-%m-%d %H:%M:%S'))

    # 用户画像（最稳定，置顶）
    profile = read_if_exists(os.path.join(HUB, 'profile.md'))
    if profile:
        parts.append('\n## 用户画像（真源 profile.md）\n')
        parts.append(profile)
        parts.append('')

    # 各分类记忆
    for fname, title in SECTION_FILES:
        content = read_if_exists(os.path.join(HUB, fname))
        if content:
            parts.append(title + '\n')
            parts.append(content)
            parts.append('')

    parts.append('## 工作规则（硬性，不可丢）\n')
    parts.append('- 唯一事实源 = <HUB>（sink.json + facts/decisions/incidents/active_tasks）。\n')
    parts.append('- 会话启动/摘要压缩后，优先重读本文件与 memory_hub 的 DIGEST.md，覆盖摘要里的冲突信息。\n')
    parts.append('- 关键结论必须写入 memory_hub（走 mem.py add），绝不只留在对话里。\n')
    parts.append('- 拿不准"记没记住"时，读文件，不靠回忆；下全称否定结论前先全盘搜索（含 E 盘等非系统盘）。\n')
    parts.append('- 每轮回答前执行 preflight.py 扫描中枢；每轮末尾执行 post_turn.py 反思沉淀。\n')

    return '\n'.join(parts)


def main():
    do_check = '--check' in sys.argv
    projection = build_projection()

    if do_check:
        old = read_if_exists(PROJECTION)
        print('差异：' + ('有变化' if old != projection else '无变化'))
        print('投影长度：%d 字符' % len(projection))
        return

    # 写投影
    os.makedirs(os.path.dirname(PROJECTION), exist_ok=True)
    with open(PROJECTION, 'w', encoding='utf-8') as f:
        f.write(projection)
    print('已写投影：%s (%d 字符)' % (PROJECTION, len(projection)))

    # 全量覆盖 ~/.workbuddy/MEMORY.md（原子写）
    os.makedirs(os.path.dirname(WORKBUDDY_MEM), exist_ok=True)
    tmp = WORKBUDDY_MEM + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(projection)
    os.replace(tmp, WORKBUDDY_MEM)
    print('已全量覆盖（原子写）：%s (%d 字符)' % (WORKBUDDY_MEM, len(projection)))


if __name__ == '__main__':
    main()
