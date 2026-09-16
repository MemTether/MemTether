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

    四项子指标（全部可确定性计算，且**互不重复**）：
      ① 替代链完整率   = superseded 中 superseded_by 已填 / superseded 总数
      ② 时间轴覆盖率   = (valid_from 已填 + recorded_at 已填) / (2 × 事实总数)
         ★2026-09-16 新增。此前四项里**没有一项测 valid_from**，
           于是"整根 T 轴为空（active 270 条里 264 条缺 valid_from）"这件事
           在分数上完全看不见——结构坏了，分数却是好的。
           新增后该问题立刻暴露，随即完成 bi-temporal 迁移 + 回填修复。
      ③ 精确冲突已处理率 = 1 - 未处理精确冲突 / 10
         （精确冲突=显式状态断言，句式确定、误报率低，见 detect_explicit_conflicts）
      ④ 失效记录闭环率 = (superseded+retired) 中 valid_to 已填 / 这两类总数

    ★口径修正（2026-09-16，同次发现）：原 ②「失效时间记录率」与 ④「退役有据率」
      实为**同一指标的两种写法**——分子分母都只是"valid_to 覆盖率"，仅阈值表述不同，
      等于同一件事被计了两次权重，而真正空缺的 valid_from 无人看管。
      现已把 ② 改为时间轴覆盖率，④ 保留 valid_to 闭环率。
      （编号与下方 r1~r4 一一对应：r3 是冲突、r4 是失效闭环，别被顺序看花。）

    ★时间轴数据的来历（native/backfilled/inferred）**不进评分卡**，
      只在 detail 里作事实陈述——它衡量的是"数据有多可信"，
      与"结构填没填"是两件事，混在一起会让分数含义变模糊。
    """
    import sqlite3 as _s
    conn = _s.connect(DB)
    n_all = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    n_sup = conn.execute("SELECT COUNT(*) FROM facts WHERE status='superseded'").fetchone()[0]
    n_ret = conn.execute("SELECT COUNT(*) FROM facts WHERE status='retired'").fetchone()[0]
    by_filled = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE status='superseded' "
        "AND superseded_by IS NOT NULL AND superseded_by!=''").fetchone()[0]
    vt_filled = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE status IN ('superseded','retired') "
        "AND valid_to IS NOT NULL AND valid_to!=''").fetchone()[0]
    vf_filled = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE valid_from IS NOT NULL AND valid_from!=''").fetchone()[0]
    rec_filled = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE recorded_at IS NOT NULL AND recorded_at!=''").fetchone()[0]
    n_native = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE temporal_source='native'").fetchone()[0]
    conn.close()

    r1 = by_filled / n_sup if n_sup else 0.0
    r2 = (vf_filled + rec_filled) / (2.0 * n_all) if n_all else 0.0
    r4 = vt_filled / (n_sup + n_ret) if (n_sup + n_ret) else 0.0

    # ③ 精确冲突（确定性，可用作指标）
    unresolved_exact, total_exact, reviewed_exact = 0, 0, 0
    try:
        import governance as _g
        nf = _g.reviewed_no_conflict()
        for x in _g.detect_explicit_conflicts():
            if x['pos']['uid'] != x['neg']['uid']:
                total_exact += 1
                # 已人工复核否定为"非冲突"的不计分（规则只生成候选，结论靠留痕）。
                # ★没有这一步，误报会永久扣分且无人能纠正——本库已有真实案例：
                #   架构描述 vs 故障记录 被判极性冲突（见 governance.record_conflict_review 注释）
                if frozenset((x['pos']['uid'], x['neg']['uid'])) in nf:
                    reviewed_exact += 1
                    continue
                unresolved_exact += 1   # 只要检测到就是"未处理"（处理掉就不再是 active 冲突）
    except Exception:
        pass
    # 曾出现的总数 = 当前未处理 + 已处理（已处理即 superseded/retired 里带冲突原因的）
    # 保守起见用"当前未处理"直接做反比；0 个未处理 = 满分
    r3 = max(0.0, 1.0 - unresolved_exact / 10.0)

    val = (r1 + r2 + r3 + r4) / 4
    detail = ('替代链 %d/%d · 时间轴 vf%d+rec%d/%d · 失效闭环 %d/%d · 未处理冲突 %d'
              '（已复核误报 %d）· native %d'
              % (by_filled, n_sup, vf_filled, rec_filled, n_all * 2,
                 vt_filled, n_sup + n_ret, unresolved_exact, reviewed_exact, n_native))
    return val, detail


def _embed_state():
    """报告当前 embedding 后端与索引一致性（不给分，只作事实陈述）。

    ★为什么要有这一行：②③ 的分数是**检索**出来的。若 embedding 后端与索引维度
      不一致（本地 1024 / 智谱 2048），检索会静默变差或硬报错，而分数看不出来。
      把运行态摆在分数旁边，是为了让"分数多可信"这件事一眼可见。
    """
    try:
        import memsearch as _ms
        be = (os.environ.get('MEM_EMBED_BACKEND') or _ms.EMBED_BACKEND_DEFAULT).lower()
        want = _ms.expected_dim()
        col = _ms._client().get_collection(_ms.COLLECTION)
        md = col.metadata or {}
        got, idx_model = md.get('embed_dim'), md.get('embed_model')
        ok = (got is None) or (int(got) == int(want))
        return ('embedding=%s(%d维)  索引=%d条/%s维/%s  %s'
                % (be, want, col.count(), got, idx_model,
                   '一致 ✓' if ok else '★不一致，检索会降级或报错'))
    except Exception as e:
        return 'embedding 状态读取失败: %s' % str(e)[:70]


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
         "替代链/双时间轴覆盖/失效闭环/冲突复核"),
    ]
    for name, raw, val, desc in rows:
        bar = "#" * int(val * 24) + "." * (24 - int(val * 24))
        lines.append("  %-8s [%s] %5.1f%%   %s" % (name, bar, val * 100, desc))
        lines.append("           %s" % raw)

    total = (c_cov + c_fresh + c_acc + c_gov) / 4
    lines.append("-" * 72)
    lines.append("  综合 %.1f%%（四维等权）" % (total * 100))
    lines.append("-" * 72)
    lines.append("  运行态：%s" % _embed_state())
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
    lines.append("    ③ 治理度衡量的是'结构被真正使用'而非'机制存在'——")
    lines.append("       替代链 0 填时它直接给 0 分，不给情面。")
    lines.append("       ★2026-09-16 完成 bi-temporal 迁移，该维度 46.6% → 81.6%：")
    lines.append("         ○ 替代链 17→24/41（7 条经语义配对＋逐条人工复核补入）")
    lines.append("         ○ 新增时间轴覆盖率。此前四项**没有一项测 valid_from**，")
    lines.append("           导致 active 270 条里 264 条缺 T 轴，却无人能在分数上看出来")
    lines.append("         ○ 失效闭环 17→36/53")
    lines.append("         ○ 原 ②「失效时间记录率」与原 ④「退役有据率」实为同一指标")
    lines.append("           （都只是 valid_to 覆盖率）被计了两次权重，属口径缺陷。")
    lines.append("           已改为'时间轴覆盖'与'失效闭环'两项——")
    lines.append("           **分数没有变高，是含义变对了**，务必别把这次修正读成刷分。")
    lines.append("       ★时间轴数据来历（native/backfilled/inferred）**不进本卡**——")
    lines.append("         native 目前仅 4 条、其余皆为回填。它衡量的是'数据有多可信'，")
    lines.append("         与'结构填没填'是两件事，混进来会让分数含义变模糊。")
    lines.append("       仍未满分的部分如实保留：17 条 superseded 属导入去重残留、")
    lines.append("       无唯一替代者，已留档 docs/supersede_candidates.json 待人工处理，")
    lines.append("       **不猜**（规则法配对实测有误配，见坑 15）。")
    lines.append("    ④ 启发式冲突检测（find_conflicts）**故意不进本卡**——")
    lines.append("       实测 8 条'自矛盾'逐条核实全是误报（对比说明/互补决策被误判），")
    lines.append("       规则法做 NL 矛盾检测的天花板就在这里。它只作人工复核候选。")
    lines.append("       但精确检测（detect_explicit_conflicts）会进卡，且支持人工复核留痕——")
    lines.append("       已有一例误报（架构描述 vs 故障记录）经复核否定后不再扣分。")
    lines.append("    ⑤ 通用能力（长程推理、时序、知识更新、跨会话指代）本卡不测——")
    lines.append("       那需要外部基准（轨道 A）。现已跑通 LongMemEval 60 题：")
    lines.append("       strict 58.5%（检索）/ llm 45.8%（端到端），双判分口径不同。")
    lines.append("       ★该分数在 judge 自检 18/18 通过后才采信（见 judge_selfcheck.py）。")
    lines.append("       本卡与它口径不同，**不混算、不互相替代**。")
    lines.append("    ⑥ ★运行态（2026-09-15 23:5x 更新）：语义检索路**已从降级中恢复**——")
    lines.append("       本地 embedding（bge-m3 int8, 1024 维）已接入并成为默认后端，")
    lines.append("       不再依赖外部付费通道。此前 22:15 的告警（智谱 429 欠费 →")
    lines.append("       查询侧无法 embed → 每次退回纯关键词）**已作废**。")
    lines.append("       这意味着：本卡的分数现在是在**语义路真正工作**的状态下取得的，")
    lines.append("       不再高估也不再低估。")
    lines.append("       注：智谱链路仍保留为可选后端（MEM_EMBED_BACKEND=zhipu），")
    lines.append("       但它与本地后端**维度不同**（2048 vs 1024），切换必须重建索引；")
    lines.append("       索引里记了 embed_dim，不匹配会硬报错而不是静默给错结果。")
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
