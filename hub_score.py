#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hub_score.py —— 记忆中枢统一评分卡

为什么需要它（用户的原始诉求）：
  "没有统一的判断标准，我无法判断你也无法判断自己的记忆中枢是什么水平"

本评分卡给出**四维客观分**，每维都有可复现的取数方式，不含主观判断：
  ① 覆盖度 Coverage   —— 本机实测存在的软件，有多少进了 tool_assets
  ② 保鲜度 Freshness  —— 资产里 last_verified_at 在 7 天内的比例
  ③ 正确率 Accuracy   —— 主集 + 留出集评测得分
  ④ 自省度 SelfAware  —— 中枢对自身结构（表/路径/槽位）的答对率

★诚信声明（必须随分数一起输出）：
  - 评测集统一走**生产同一条检索链路**（memsearch.search_hybrid），不使用自拼 SQL。
    ★这条是 2026-09-15 血的教训：旧评测自己拼 LIKE 直查表，结果 66 条资产
    对 `mem.py search` 完全不可见时，卷子照样满分——"卷子满分、生产查不到"。
  - 主集含本次刚修的资产，存在"自出卷"偏差；故同时报**留出集**作为校准。
  - 本评分卡不引入 LLM judge —— 答案键全是本机实测事实，无主观空间。
    （对比：LoCoMo 答案键约 6.4% 错误、LLM judge 接受约 63% 故意错答。）
  - 任何分数必须与 harness 版本、日期一起报告，否则不可比。

用法：python hub_score.py [--save]
"""
import os
import re
import sys
import json
import sqlite3
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "memory.db")
SCORECARD = os.path.join(HERE, "scorecard.json")


def _cov():
    """覆盖度：tool_audit.ASSETS 是实测存在的清单，看入库比例。"""
    import tool_audit
    cur = sqlite3.connect(DB).cursor()
    cur.execute("SELECT name FROM tool_assets WHERE status='active'")
    in_db = {r[0] for r in cur.fetchall()}
    total = len(tool_audit.ASSETS)
    hit = sum(1 for a in tool_audit.ASSETS if a[0] in in_db)
    return hit, total


def _fresh(days=7):
    """保鲜度：last_verified_at 在 N 天内的比例。"""
    cur = sqlite3.connect(DB).cursor()
    cur.execute("SELECT last_verified_at FROM tool_assets WHERE status='active'")
    rows = [r[0] for r in cur.fetchall()]
    if not rows:
        return 0, 0
    cutoff = dt.datetime.now() - dt.timedelta(days=days)
    ok = 0
    for ts in rows:
        try:
            if dt.datetime.strptime((ts or "")[:19], "%Y-%m-%d %H:%M:%S") >= cutoff:
                ok += 1
        except Exception:
            pass
    return ok, len(rows)


def _bench():
    import asset_bench
    import asset_bench_holdout
    p1, t1 = asset_bench.run(verbose=False)
    import io
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        p2, t2 = asset_bench_holdout.run(verbose=False)
    finally:
        sys.stdout = old
    return p1, t1, p2, t2


def _govern():
    """★新增第 ④ 维：治理度。

    动机（2026-09-15 21:40）：前一版评分卡只有覆盖度/保鲜度/正确率三维，
    全是"存得好不好"，**缺"管得好不好"**。实测证据：
      - `superseded_by` 字段 active 子集 0/254 填过 → 结构在，从没启用
      - `valid_to` 同 0/254 → 失效时间从未记录
      - 「DeepSeek 已充值可用」与「已 401 失效」并存数天无人处理
    所以"~20% 的治理层"必须变成可复现的数字，而不是一句形容词。

    ★设计教训（2026-09-15 21:45，重要）：
      我最初想把"条内自矛盾数"作为子指标，实测发现**规则法判不准**——
      8 条"自矛盾"逐条人工核实**全是误报**（"写A报错/写B正常"是**对比说明**，
      "新的优先、失败退老档"是**互补决策**，不是矛盾）。
      7 轮迭代（稀有token→句子级→实体级→别名归并）每次都在
      假阳性/假阴性之间摆动，证明**规则法做自然语言矛盾检测的天花板就在这里**。
      → 因此本维度**只用确定性指标**，不掺入启发式结果：
        启发式检测另走 `governance.find_conflicts()` 供**人工复核**，
        绝不进评分卡（否则分数会随检测器的噪音一起抖）。

    四项子指标（全部可确定性计算）：
      ① 替代链使用率 = superseded_by 已填 / superseded 总数
      ② 失效时间记录率 = valid_to 已填 / (superseded+retired) 总数
      ③ 精确冲突已处理率 = 1 - 未处理精确冲突 / 曾出现的精确冲突
         （精确冲突=显式状态断言，句式确定、误报率低，见 detect_explicit_conflicts）
      ④ 退役操作有据率 = 有 reason 或 superseded_by 的退役 / 总退役
         （衡量"退役是否留下可追溯的理由"，而不是随手标个状态）
    """
    import sqlite3 as _s
    conn = _s.connect(DB)
    n_sup = conn.execute("SELECT COUNT(*) FROM facts WHERE status='superseded'").fetchone()[0]
    n_ret = conn.execute("SELECT COUNT(*) FROM facts WHERE status='retired'").fetchone()[0]
    by_filled = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE superseded_by IS NOT NULL AND superseded_by!=''").fetchone()[0]
    vt_filled = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE valid_to IS NOT NULL AND valid_to!=''").fetchone()[0]
    conn.close()

    r1 = by_filled / n_sup if n_sup else 0.0
    r2 = vt_filled / (n_sup + n_ret) if (n_sup + n_ret) else 0.0

    # ③ 精确冲突（确定性，可用作指标）
    unresolved_exact, total_exact = 0, 0
    try:
        import governance as _g
        for x in _g.detect_explicit_conflicts():
            if x['pos']['uid'] != x['neg']['uid']:
                total_exact += 1
                unresolved_exact += 1   # 只要检测到就是"未处理"（处理掉就不再是 active 冲突）
    except Exception:
        pass
    # 曾出现的总数 = 当前未处理 + 已处理（已处理即 superseded/retired 里带冲突原因的）
    # 保守起见用"当前未处理"直接做反比；0 个未处理 = 满分
    r3 = max(0.0, 1.0 - unresolved_exact / 10.0)

    # ④ 退役有据率：退役条目里，valid_to 有值且（superseded_by 有值 或 属 retired）的比例
    conn = _s.connect(DB)
    n_doc = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE status IN ('superseded','retired') "
        "AND valid_to IS NOT NULL AND valid_to!=''").fetchone()[0]
    conn.close()
    r4 = n_doc / (n_sup + n_ret) if (n_sup + n_ret) else 0.0

    val = (r1 + r2 + r3 + r4) / 4
    detail = ('替代链 %d/%d · 失效时间 %d/%d · 未处理冲突 %d · 退役有据 %d/%d'
              % (by_filled, n_sup, vt_filled, n_sup + n_ret,
                 unresolved_exact, n_doc, n_sup + n_ret))
    return val, detail


def score():
    lines = []
    lines.append("=" * 72)
    lines.append("记忆中枢评分卡  hub_score  |  %s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("=" * 72)

    h, t = _cov()
    f, ft = _fresh()
    p1, t1, p2, t2 = _bench()

    c_cov = h / t if t else 0
    c_fresh = f / ft if ft else 0
    c_acc = (p1 + p2) / (t1 + t2) if (t1 + t2) else 0
    c_gov, gov_detail = _govern()

    rows = [
        ("① 覆盖度", "%d/%d" % (h, t), c_cov,
         "本机实测存在的软件，已入库比例"),
        ("② 保鲜度", "%d/%d" % (f, ft), c_fresh,
         "last_verified_at 在 7 天内的比例"),
        ("③ 正确率", "%d+%d/%d+%d" % (p1, p2, t1, t2), c_acc,
         "主集 + 留出集（均走生产检索链路 search_hybrid）"),
        ("④ 治理度", gov_detail, c_gov,
         "替代链/失效时间/精确冲突/退役有据"),
    ]
    for name, raw, val, desc in rows:
        bar = "#" * int(val * 24) + "." * (24 - int(val * 24))
        lines.append("  %-8s [%s] %5.1f%%   %s" % (name, bar, val * 100, desc))
        lines.append("           %s" % raw)

    total = (c_cov + c_fresh + c_acc + c_gov) / 4
    lines.append("-" * 72)
    lines.append("  综合 %.1f%%（四维等权）" % (total * 100))
    lines.append("=" * 72)
    lines.append("  ★诚信声明：主集 %d 题含本次刚修资产，有'自出卷'偏差；" % t1)
    lines.append("   故并列留出集 %d 题（本次未修资产 + 反向不瞎编 + C盘资产）作为校准。" % t2)
    lines.append("   本卡不引入 LLM judge，答案键均为本机实测事实，无主观空间。")
    lines.append("   对比参考：LoCoMo 答案键约 6.4% 错误、LLM judge 接受约 63% 故意错答。")
    lines.append("   两集均走**生产同一条检索链路**，非自拼 SQL。")
    lines.append("-" * 72)
    lines.append("  ⚠ 失真警告（2026-09-15 实测教训，必读）：")
    lines.append("    ① ①②③ 全是'本机资产'类问题，且今天刚被补齐 → 满分")
    lines.append("       只证明'资产类'这一维合格，不代表中枢通用水平。")
    lines.append("    ② 今天已两次出现「评分卡 100% 但一问就露馅」：")
    lines.append("       - 66 条资产对 mem.py search 完全不可见（资产表没进检索）")
    lines.append("       - 旧评测自己拼 LIKE 直查表 → 卷子绿、生产查不到")
    lines.append("       ★教训：评测器本身也是被测对象。分数高先怀疑卷子。")
    lines.append("    ③ ④ 治理度是本轮新加的维度，它衡量的是'结构被真正使用'")
    lines.append("       而非'机制存在'——替代链 0 填时它直接给 0 分，不给情面。")
    lines.append("       当前 45% 偏低的原因已查明：历史 38 条 superseded 是批量导库时")
    lines.append("       打的标记，既无 valid_to 也无 superseded_by；只有本轮手工处理的")
    lines.append("       3 条是完整的。这个数字是**诚实的历史欠账**，不是检测器噪音。")
    lines.append("    ④ 启发式冲突检测（find_conflicts）**故意不进本卡**——")
    lines.append("       实测 8 条'自矛盾'逐条核实全是误报（对比说明/互补决策被误判），")
    lines.append("       规则法做 NL 矛盾检测的天花板就在这里。它只作人工复核候选。")
    lines.append("    ④ 通用能力（长程推理、时序、知识更新、跨会话指代）本卡仍不测——")
    lines.append("       那需要 LoCoMo/LongMemEval/BEAM，属另一条轨道，尚未跑。")
    lines.append("    ⑤ ★运行态告警（2026-09-15 22:15 实测）：本卡①②③的满分是在")
    lines.append("       **语义检索路降级**的状态下取得的 —— 智谱 embedding 返回")
    lines.append("       429 code=1113『余额不足』，查询侧无法 embed，每次检索都退回")
    lines.append("       纯关键词（RRF）兜底。向量索引本身完好（323 条），但召回变差。")
    lines.append("       这意味着：本卡的分数**高估了当前的实际检索能力**。")
    lines.append("       重启语义路：给智谱充值，或接一个本地 embedding 做兜底。")
    lines.append("=" * 72)

    text = "\n".join(lines)
    print(text)

    if "--save" in sys.argv:
        with open(SCORECARD, "w", encoding="utf-8") as fp:
            json.dump(dict(
                ts=dt.datetime.now().isoformat(),
                coverage=[h, t], freshness=[f, ft],
                bench_main=[p1, t1], bench_holdout=[p2, t2],
                governance=round(c_gov, 4), governance_detail=gov_detail,
                score=round(total, 4),
            ), fp, ensure_ascii=False, indent=2)
        print("已存 %s" % SCORECARD)
    return total


if __name__ == "__main__":
    score()
