# -*- coding: utf-8 -*-
"""migrate_bitemporal.py —— 把 facts 表升级为双时间轴（bi-temporal）模型

【为什么做这件事（2026-09-16）】
  参考 getzep/graphiti 的 bi-temporal 设计，它明确指出：
    "each fact in a context graph has a validity window: when it became true,
     and when (if ever) it was superseded."
    "Facts have validity windows. When information changes, old facts are
     invalidated — not deleted. Query what's true now, or what was true at any
     point in time."
  我们此前只有**单轴**（valid_from/valid_to），且实际长期为空：
    active 270 条里 264 条 valid_from 为空 → 整根轴形同虚设。
  更严重的是评分卡四个治理子指标**没有一项测 valid_from**，
  所以"结构损坏"这件事在分数上完全看不见。

【两条轴的分工（务必分清，别混）】
  T 轴（有效时间 / valid time）——"这件事在现实世界里何时成立"
      valid_from : 现实世界开始成立的时间
      valid_to   : 现实世界停止成立的时间（NULL = 仍成立）
  T'轴（摄录时间 / transaction time）——"系统何时知道 / 何时认定它失效"
      recorded_at    : 系统第一次记录该事实的时刻
      invalidated_at : 系统第一次认定该事实失效的时刻

  两轴分离的价值（用本库真实例子说明）：
    "DeepSeek 官方 API 已充值可用" 现实里 2026-09-15 就挂了（valid_to），
    但我们是当晚 21:07 才发现/落库（invalidated_at）。
    → 问"09-15 中午系统认为什么为真"应回答"可用"——因为它当时还不知道。
      这正是 T' 轴存在的意义：能解释"当时为什么那样决策"。若只有单轴，
      历史查询会得出"那天它已经知道失效了"的错误结论。

【回填依据分级（不把推定当确凿）】
  新增 temporal_source 记录该条时间轴数据的**最弱环节**：
    native     写入时原生记录（迁移后新产生的数据）
    backfilled 从既有字段精确迁移，语义等价、不靠猜
    inferred   合理推定，但无直接证据
  规则：
    recorded_at = created_at            → backfilled（定义即如此，非猜测）
    valid_to    = 替代者的 created_at    → backfilled（替代发生那一刻即失效，确凿）
    valid_from  = created_at            → inferred （记录时认为成立；事实可能更早成立）
    invalidated_at = 无 valid_to 者取 updated_at（仅当 != created_at）→ inferred
    superseded_by 无依据者               → 留空，**不猜**（见配对报告）

用法：
  python migrate_bitemporal.py --check     只查现状，不改
  python migrate_bitemporal.py --apply     执行（自动备份）
"""
import os
import shutil
import sqlite3
import sys
import time

HUB = r'<HUB>'
DB = os.path.join(HUB, 'memory.db')

NEW_COLS = [
    ('recorded_at', 'TEXT'),      # T'轴：系统摄录时间
    ('invalidated_at', 'TEXT'),   # T'轴：系统认定失效时间
    ('temporal_source', 'TEXT'),  # 时间轴数据来历 native/backfilled/inferred
]

INDEXES = [
    ('idx_facts_valid_from', 'facts(valid_from)'),
    ('idx_facts_recorded_at', 'facts(recorded_at)'),
]


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def cols(c):
    return {r[1] for r in c.execute('PRAGMA table_info(facts)')}


def check():
    c = conn()
    have = cols(c)
    print('=== 现状检查 ===')
    for name, _ in NEW_COLS:
        print('  %-16s %s' % (name, '已存在' if name in have else '★缺失，需迁移'))
    print()
    n_act = c.execute("SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]
    n_vf = c.execute("SELECT COUNT(*) FROM facts WHERE status='active' "
                     "AND (valid_from IS NULL OR valid_from='')").fetchone()[0]
    n_sup = c.execute("SELECT COUNT(*) FROM facts WHERE status='superseded'").fetchone()[0]
    n_vt = c.execute("SELECT COUNT(*) FROM facts WHERE status='superseded' "
                     "AND (valid_to IS NULL OR valid_to='')").fetchone()[0]
    print('  active %d 条，其中缺 valid_from %d 条' % (n_act, n_vf))
    print('  superseded %d 条，其中缺 valid_to %d 条' % (n_sup, n_vt))
    # 可精确回填的：superseded_by 指向的目标存在
    fixable = 0
    for r in c.execute("SELECT uid,superseded_by FROM facts WHERE status='superseded' "
                       "AND superseded_by IS NOT NULL AND superseded_by!='' "
                       "AND (valid_to IS NULL OR valid_to='')"):
        t = c.execute('SELECT created_at FROM facts WHERE uid=?', (r['superseded_by'],)).fetchone()
        if t and t['created_at']:
            fixable += 1
    print('  其中「替代者存在」可精确回填 valid_to 的: %d 条' % fixable)
    c.close()


def migrate(apply=False):
    c = conn()
    have = cols(c)
    todo = [(n, t) for n, t in NEW_COLS if n not in have]
    if not todo and not apply:
        check()
        return

    if not apply:
        print('=== 待执行迁移（dry-run）===')
        for n, t in todo:
            print('  ALTER TABLE facts ADD COLUMN %s %s' % (n, t))
        print('  然后执行回填（见 --apply 输出）')
        return

    # ---- 备份 ----
    bak = os.path.join(HUB, 'backup', 'memory.db.pre-bitemporal-%s' % time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    shutil.copy2(DB, bak)
    print('已备份 → %s' % bak)

    # ---- 加列 ----
    for n, t in todo:
        c.execute('ALTER TABLE facts ADD COLUMN %s %s' % (n, t))
        print('  + 列 %s %s' % (n, t))
    for idx, target in INDEXES:
        c.execute('CREATE INDEX IF NOT EXISTS %s ON %s' % (idx, target))
    c.commit()

    # ---- 回填 1: recorded_at = created_at（定义即如此）----
    n = c.execute("UPDATE facts SET recorded_at=created_at, "
                  "temporal_source=COALESCE(temporal_source,'backfilled') "
                  "WHERE (recorded_at IS NULL OR recorded_at='') AND created_at IS NOT NULL "
                  "AND created_at!=''").rowcount
    c.commit()
    print('\n[回填1] recorded_at ← created_at : %d 条 (backfilled)' % n)

    # ---- 回填 2: valid_from = created_at（推定，覆盖所有状态）----
    # ★修正（同次）：初版只填 status='active'，漏掉了 superseded/retired——
    #   它们同样"曾经成立过"，没有 valid_from 就无法回答"它在哪段时间有效"。
    #   全部状态统一处理。
    n = c.execute("UPDATE facts SET valid_from=created_at, "
                  "temporal_source=CASE WHEN temporal_source='native' THEN 'native' ELSE 'inferred' END "
                  "WHERE (valid_from IS NULL OR valid_from='') "
                  "AND created_at IS NOT NULL AND created_at!=''").rowcount
    c.commit()
    print('[回填2] valid_from ← created_at : %d 条 (inferred，覆盖所有状态)' % n)

    # ---- 回填 3: superseded 的 valid_to = 替代者 created_at（确凿）----
    n = 0
    rows = c.execute("SELECT uid,superseded_by FROM facts WHERE status='superseded' "
                     "AND superseded_by IS NOT NULL AND superseded_by!='' "
                     "AND (valid_to IS NULL OR valid_to='')").fetchall()
    for r in rows:
        t = c.execute('SELECT created_at FROM facts WHERE uid=?', (r['superseded_by'],)).fetchone()
        if t and t['created_at']:
            c.execute('UPDATE facts SET valid_to=?, invalidated_at=?, temporal_source=? WHERE uid=?',
                      (t['created_at'], t['created_at'], 'backfilled', r['uid']))
            n += 1
    c.commit()
    print('[回填3] superseded.valid_to ← 替代者created_at : %d 条 (backfilled，确凿)' % n)

    # ---- 回填 4: 其余失效记录的 invalidated_at ----
    n = c.execute("UPDATE facts SET invalidated_at=valid_to "
                  "WHERE status IN ('superseded','retired') AND valid_to IS NOT NULL AND valid_to!='' "
                  "AND (invalidated_at IS NULL OR invalidated_at='')").rowcount
    c.commit()
    print('[回填4] invalidated_at ← valid_to : %d 条 (backfilled)' % n)

    n = c.execute("UPDATE facts SET invalidated_at=updated_at, temporal_source='inferred' "
                  "WHERE status IN ('superseded','retired','quarantined') "
                  "AND (invalidated_at IS NULL OR invalidated_at='') "
                  "AND updated_at IS NOT NULL AND updated_at!='' AND updated_at!=created_at").rowcount
    c.commit()
    print('[回填5] invalidated_at ← updated_at : %d 条 (inferred，updated_at≠created_at 才可信)' % n)

    # ---- 汇总 ----
    print('\n=== 回填后 ===')
    for st in ('active', 'superseded', 'retired', 'quarantined'):
        r = c.execute("SELECT COUNT(*) n, "
                      "SUM(recorded_at IS NOT NULL AND recorded_at!='') rec, "
                      "SUM(valid_from IS NOT NULL AND valid_from!='') vf, "
                      "SUM(valid_to IS NOT NULL AND valid_to!='') vt "
                      "FROM facts WHERE status=?", (st,)).fetchone()
        if r['n']:
            print('  %-12s 共%3d  recorded %3d  valid_from %3d  valid_to %3d'
                  % (st, r['n'], r['rec'], r['vf'], r['vt']))
    print('\n=== temporal_source 分布 ===')
    for r in c.execute("SELECT COALESCE(temporal_source,'(空)') s, COUNT(*) n FROM facts "
                       "GROUP BY temporal_source ORDER BY n DESC"):
        print('  %-12s %d' % (r['s'], r['n']))
    c.close()


if __name__ == '__main__':
    if '--apply' in sys.argv:
        migrate(apply=True)
    else:
        check()
        print()
        migrate(apply=False)
