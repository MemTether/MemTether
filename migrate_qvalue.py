# -*- coding: utf-8 -*-
"""给 facts 表加 q_value / use_count 两列（升级 1：Q-Value）。

【为什么加这两列】
  本项目长期卡在记忆生命周期前 1/3：写入做得好，但「哪条记忆真的有用」这个信号
  从未被记录，于是投影与检索只能靠时间 / 相似度排序，历史高频复用的结论会被
  新写入挤下去。Q-Value 是 ROI 最高的一步：让**被反复采纳的记忆**在检索里上浮。

【列语义】
  q_value   REAL    默认 0.5 —— 条目价值分，中性起步。检索时作为乘性因子
                               (0.3 + 0.7*q_value)；回写公式 q += 0.1*(reward - q)。
  use_count INTEGER 默认 0   —— 被采纳次数，纯计数、不参与打分（可审计用）。

【安全性】
  · 幂等：列已存在则跳过，不重复加、不报错。
  · 只做 ALTER TABLE ADD COLUMN（SQLite 元数据级操作，毫秒级、事务性）。
  · 全部常量默认值 → 已有行读出来就是 0.5 / 0，不重写任何行内容。
  · 已确认：仓库内所有 `INSERT INTO facts` 都是**显式列名**，
    所有 `SELECT * FROM facts` 都是**具名字段**访问 —— 加列不会造成位置错位。

【和 gateway.init_db() 的关系】
  gateway.init_db() 里已内置等价的幂等自动迁移（_ensure_columns）——
  正常升级路径**不需要**手跑本脚本。本脚本的用途是：
    ① 加列前先**看一眼**当前库状态（默认只读，不改任何东西）；
    ② 需要显式落库时用 --apply；
    ③ 排障：确认某台机器上的库到底有没有这两列。

用法：
  python migrate_qvalue.py            # 只检查，不改库（默认）
  python migrate_qvalue.py --apply    # 真的执行 ALTER

退出码：
  0  CHECK 模式：两列已齐备，无需迁移
  1  CHECK 模式：缺列，需要迁移（本次未改动库）
  0  APPLY 模式：加列成功
  1  APPLY 模式：加列异常
  2  库不存在 / 用法错误
"""
import argparse
import os
import sqlite3
import sys

HUB = os.path.dirname(os.path.abspath(__file__))
# ★与 gateway.py / mem.py / memsearch.py 同一套 MEM_DB 规则 —— 各自硬编码就会出现
#   "同一轮查询读两个库"的错。
DB = os.environ.get('MEM_DB') or os.path.join(HUB, 'memory.db')

NEW_COLS = [
    ('q_value', 'REAL', '0.5'),
    ('use_count', 'INTEGER', '0'),
]


def cols_of(con):
    return {r[1]: {'type': r[2], 'notnull': r[3], 'dflt': r[4]}
            for r in con.execute('PRAGMA table_info(facts)')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='真的执行 ALTER（默认只检查，不改库）')
    a = ap.parse_args()

    if not os.path.exists(DB):
        print('库不存在: %s' % DB)
        return 2
    print('目标库: %s (%d B)' % (DB, os.path.getsize(DB)))
    print('模式  : %s' % ('APPLY（会写库）' if a.apply else 'CHECK（只读，不改库）'))

    con = sqlite3.connect(DB)
    try:
        before = cols_of(con)
        n_before = con.execute('SELECT COUNT(*) FROM facts').fetchone()[0]
        a_before = con.execute(
            "SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]
        print('加列前: facts=%d  active=%d  列数=%d' % (n_before, a_before, len(before)))

        changed = False
        for name, typ, dflt in NEW_COLS:
            if name in before:
                print('  [跳过] %s 已存在（type=%s dflt=%s）'
                      % (name, before[name]['type'], before[name]['dflt']))
                continue
            sql = 'ALTER TABLE facts ADD COLUMN %s %s DEFAULT %s' % (name, typ, dflt)
            if a.apply:
                con.execute(sql)
                changed = True
                print('  [新增] %s' % sql)
            else:
                print('  [缺失] %s   → 需要执行：%s' % (name, sql))
        if changed:
            con.commit()
            print('  已提交。')

        after = cols_of(con)
        n_after = con.execute('SELECT COUNT(*) FROM facts').fetchone()[0]
        a_after = con.execute(
            "SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]

        print('\n加列后: facts=%d  active=%d  列数=%d' % (n_after, a_after, len(after)))
        print('  条数不变: %s' % (n_before == n_after and a_before == a_after))

        # ★先算出「到底缺不缺列」，结论行必须反映**真实状态**，不能只看 ok。
        missing = [n for n, _, _ in NEW_COLS if n not in after]
        ok = True
        for name, typ, dflt in NEW_COLS:
            c = after.get(name)
            if not c:
                print('  [待补] %s 不存在%s'
                      % (name, '' if a.apply else '（CHECK 模式，未写入）'))
                ok = ok and (not a.apply)
                continue
            hit = (c['type'].upper() == typ) and str(c['dflt']) == dflt
            print('  %s type=%s dflt=%s  %s'
                  % (name, c['type'], c['dflt'], 'OK' if hit else 'FAIL'))
            ok = ok and hit

        # 实际读值校验：已有行读出来必须是默认值
        if not missing:
            qv = con.execute('SELECT COUNT(*) FROM facts WHERE q_value = 0.5').fetchone()[0]
            uc = con.execute('SELECT COUNT(*) FROM facts WHERE use_count = 0').fetchone()[0]
            print('  q_value=0.5 的行数: %d / %d' % (qv, n_after))
            print('  use_count=0 的行数: %d / %d' % (uc, n_after))
            print('  integrity_check: %s' % con.execute('PRAGMA integrity_check').fetchone()[0])
            ok = ok and qv == n_after and uc == n_after
        elif not a.apply:
            print('\n（CHECK 模式：以上未落库。加 --apply 执行，或直接跑 gateway.py init 自动补齐）')

        # ★结论行必须与真实状态一致。
        #   早先这里写死 `'加列成功 ✓' if ok else ...`，而 CHECK 模式下缺列时 ok 仍为 True
        #   （`ok = ok and (not a.apply)`），于是出现「明细说未写入、结论说成功」——
        #   典型的假成功信号。改成按模式分别给结论。
        if a.apply:
            verdict = '加列成功 ✓' if ok else '加列异常 ✗'
        elif missing:
            verdict = '需迁移：缺 %s（本次只读，未改动库）' % '、'.join(missing)
        else:
            verdict = '无需迁移：两列均已存在'
        print('\n结论:', verdict)

        # 退出码：0 = 无需动作 / 动作成功；1 = 需要迁移（CHECK）或加列异常（APPLY）
        if a.apply:
            return 0 if ok else 1
        return 1 if missing else 0
    finally:
        con.close()


if __name__ == '__main__':
    sys.exit(main())
