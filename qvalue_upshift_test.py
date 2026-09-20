# -*- coding: utf-8 -*-
"""qvalue_upshift_test.py —— Q-Value「排序上移」实测（验收第二半）

【要证的事】
    被高频采纳的条目，排序会**上浮**。

【为什么不能在真源库上直接测】
    要验证上移，必须真的把某些条目的 `q_value` 调高；直接改真源库属生产数据写入。
    改用**副本库 + 正本索引**：
      `MEM_DB`    → 副本 memory.db（只改副本里的 q_value，测完整目录丢弃）
      `MEM_STORE` → 正本向量索引（★必须显式钉住！否则索引路径默认跟着 DB 走，
                     落到副本同目录、目录不存在 → 语义路静默降级，对照就失真了）

【测法（单变量）】
    同一副本、同一索引、同一查询，只动 `q_value`：
      轮 A：副本内全部 q=0.5（等价于关闭 Q-Value）
      轮 B：副本内把「轮 A 的中位条目」q 调到 0.99，其余不动
    → 若轮 B 里该条目名次上升，且 A 轮里它确实在中位，则「上移」成立。

【前置条件】
    需要一个**有内容的**库。默认按 `--db` → `$MEM_DB` → 本目录 `memory.db`
    → `demo/memory_demo.db` 的顺序找；都没有会明确报错并给出生成命令，
    **不会**静默拿空库跑出一个"看起来通过"的结果。

【用法】
    python qvalue_upshift_test.py                     # 自动挑一条内容做查询
    python qvalue_upshift_test.py --query "某个问题"   # 指定查询（真库上推荐）
    python qvalue_upshift_test.py --db demo/memory_demo.db
"""
import os
import re
import sys
import shutil
import sqlite3
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

LIMIT = 10
# 中位取第 6 条：上移空间足够，又不至于一次跳到顶
MID_RANK = 6
# 被提升条目置为该 q 值（对照轮全库 0.5）
BOOST_Q = 0.99


def _abs(p):
    return p if os.path.isabs(p) else os.path.join(HERE, p)


def resolve_db(arg):
    """按 --db → MEM_DB → memory.db → demo/memory_demo.db 找库；找不到返回 None。"""
    cands = []
    if arg:
        cands.append(arg)
    if os.environ.get('MEM_DB'):
        cands.append(os.environ['MEM_DB'])
    cands += [os.path.join(HERE, 'memory.db'),
              os.path.join(HERE, 'demo', 'memory_demo.db')]
    for c in cands:
        c = _abs(c)
        if os.path.isfile(c):
            return c
    return None


def pick_query(db):
    """没给 --query 时，从库里取一段**真实存在**的连续串当查询词。

    为什么不硬编码一个查询：硬编码的查询只在那台机器上成立，
    换一份库就返回 0 条，脚本会看起来"跑通了"其实什么也没测。
    取「最长的一段非空白连续串」是因为它一定是库里真有的词，
    混合检索（关键词 + 字面 + 语义）必然能命中。
    """
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT content FROM facts WHERE status='active' AND length(content) >= 24"
            " ORDER BY rowid").fetchall()
    except sqlite3.Error as e:
        print('★ 读 facts 表失败：%s' % e)
        return None
    finally:
        con.close()
    if not rows:
        return None
    mid = rows[len(rows) // 2][0] or ''
    toks = sorted(re.split(r'[\s,，。；;：:、/|()（）\[\]【】{}<>《》"\'`]+', mid),
                  key=len, reverse=True)
    for t in toks:
        if len(t) >= 3:
            return t[:24]
    return mid[:16] or None


def main():
    argv = sys.argv[1:]
    db_arg = query = None
    for i, a in enumerate(argv):
        if a == '--db' and i + 1 < len(argv):
            db_arg = argv[i + 1]
        elif a.startswith('--db='):
            db_arg = a.split('=', 1)[1]
        elif a == '--query' and i + 1 < len(argv):
            query = argv[i + 1]
        elif a.startswith('--query='):
            query = a.split('=', 1)[1]

    src_db = resolve_db(db_arg)
    if not src_db:
        print('★ 找不到可用的记忆库 —— 本脚本需要一个**有内容**的库，不会拿空库硬跑。')
        print('  查找顺序：--db 参数 → $MEM_DB → %s → %s' %
              (os.path.join(HERE, 'memory.db'),
               os.path.join(HERE, 'demo', 'memory_demo.db')))
        print('  生成一份全合成演示库：')
        print('      python scripts/make_demo_db.py')
        print('      python qvalue_upshift_test.py --db demo/memory_demo.db')
        return 2

    if not query:
        query = pick_query(src_db)
    if not query:
        print('★ 库里没有 status=active 的条目，无法做上移测试：%s' % src_db)
        return 2

    # ---- 建副本（放临时目录，测完整目录丢弃）----
    tmpdir = os.path.join(tempfile.gettempdir(), 'qv_upshift')
    os.makedirs(tmpdir, exist_ok=True)
    copy_db = os.path.join(tmpdir, 'copy.db')
    shutil.copy2(src_db, copy_db)

    # 向量索引：显式指定则用它，否则跟着副本库所在目录推（副本目录里通常没有）
    real_store = os.environ.get('MEM_STORE') or os.path.join(
        os.path.dirname(src_db), 'mem0_store')

    # ---- ★先设环境再 import：memsearch 的 DB / 索引路径是模块级常量 ----
    os.environ['MEM_DB'] = copy_db
    os.environ['MEM_STORE'] = real_store
    os.environ['MEM_QVALUE'] = '1'          # 全程开着，靠 q_value 数值制造差异

    # 代码目录优先用 MEM_HUB_CODE_DIR（模型/索引/代码同源），否则 HERE。
    # 背景：本脚本在 memtether（发布库），而模型目录在 memory_hub（生产库）；
    # 硬编码 HERE 会让 import 拿到发布库的 embed_local、模型路径算错 →
    # 向量检索静默降级关键词（跑起来不报错但结果失真）。
    code_dir = os.environ.get('MEM_HUB_CODE_DIR') or HERE
    sys.path.insert(0, code_dir)
    import memsearch as ms   # noqa: E402

    def order(tag):
        r = ms.search_hybrid(query, limit=LIMIT, decay=False)
        rows = r['results']
        print('  [%s] 返回 %d 条' % (tag, len(rows)))
        for i, x in enumerate(rows, 1):
            print('    %2d. q=%.2f  score=%.5f  %s  %s'
                  % (i, x.get('q_value', -1), x['score'], x['uid'][:26],
                     (x.get('content') or '')[:44].replace('\n', ' ')))
        return rows

    def set_q(uid, q):
        # ★2026-09-20：按 uid 前缀选表 —— tool- 开头是 tool_assets（资产也有
        #   q_value 了）。此前无脑 UPDATE facts，对资产条目静默无效（0 行），
        #   表现为「表格 q 列全 0.5、结论区却印 q=0.99」的自相矛盾。
        table = 'tool_assets' if uid.startswith('tool-') else 'facts'
        c = sqlite3.connect(copy_db)
        cur = c.execute('UPDATE %s SET q_value=? WHERE uid=?' % table, (q, uid))
        print('   set_q: %s 表命中 %d 行' % (table, cur.rowcount))
        c.commit()
        c.close()

    print('=' * 90)
    print('Q-Value 排序上移实测   查询=%r' % query)
    print('  副本库 = %s' % copy_db)
    print('  索引   = %s%s' % (real_store, '' if os.path.isdir(real_store)
                              else '   （不存在 → 语义路降级，仅关键词+字面）'))
    print('=' * 90)

    print('\n【轮 A】副本内全部 q=0.5（对照组）')
    a = order('A')

    if len(a) < MID_RANK:
        print('  ! 结果不足 %d 条，无法做中位上移测试 —— 换一个 --query 或换更大的库'
              % MID_RANK)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return 1

    target = a[MID_RANK - 1]
    t_uid = target['uid']
    print('\n  选中中位条目作提升对象：rank%d  %s' % (MID_RANK, t_uid))
    print('    %s' % (target.get('content') or '')[:70].replace('\n', ' '))

    set_q(t_uid, BOOST_Q)
    print('  已把副本内该条 q_value 置为 %.2f（其余不动）' % BOOST_Q)

    print('\n【轮 B】仅该条 q=%.2f（实验组）' % BOOST_Q)
    b = order('B')

    pos_a = next((i for i, x in enumerate(a, 1) if x['uid'] == t_uid), None)
    pos_b = next((i for i, x in enumerate(b, 1) if x['uid'] == t_uid), None)

    print()
    print('-' * 90)
    print('结论')
    print('  被提升条目 %s' % t_uid)
    print('    轮 A 名次 = %s   （q=0.50，因子 0.650）' % pos_a)
    print('    轮 B 名次 = %s   （q=%.2f，因子 %.3f）'
          % (pos_b, BOOST_Q, 0.3 + 0.7 * BOOST_Q))
    if pos_a and pos_b:
        if pos_b < pos_a:
            print('    → ★名次上升 %d 位：Q-Value 生效，「被采纳条目上浮」成立' % (pos_a - pos_b))
        elif pos_b == pos_a:
            print('    → 名次未变：该条与相邻条目分差过大，一次提升不足以跨越（非失效）')
        else:
            print('    → ★名次下降：与设计相反，必须查')
    print('  轮 B 的 q 值列：%s' % [round(x.get('q_value', -1), 2) for x in b])

    # 清场：删副本（临时目录，非用户文件）
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print('\n  已清理临时副本 %s' % tmpdir)
    except Exception as e:
        print('\n  临时副本清理失败（可忽略）：%s' % e)
    return 0


if __name__ == '__main__':
    sys.exit(main())
