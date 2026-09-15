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
  - 主集 23 题覆盖了 2026-09-15 刚修的资产，存在"自出卷"偏差；
    故同时报**留出集**（14 题，全为本次未修的资产 + 反向不瞎编题）作为校准。
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

    rows = [
        ("① 覆盖度", "%d/%d" % (h, t), c_cov,
         "本机实测存在的软件，已入库比例"),
        ("② 保鲜度", "%d/%d" % (f, ft), c_fresh,
         "last_verified_at 在 7 天内的比例"),
        ("③ 正确率", "%d+%d/%d+%d" % (p1, p2, t1, t2), c_acc,
         "主集+留出集，答案键为本机实测事实"),
    ]
    for name, raw, val, desc in rows:
        bar = "#" * int(val * 24) + "." * (24 - int(val * 24))
        lines.append("  %-8s %-10s [%s] %5.1f%%   %s" % (name, raw, bar, val * 100, desc))

    total = (c_cov + c_fresh + c_acc) / 3
    lines.append("-" * 72)
    lines.append("  综合 %.1f%%（三维等权；④自省度已含在③的主集 C 类中）" % (total * 100))
    lines.append("=" * 72)
    lines.append("  ★诚信声明：主集 %d 题含本次刚修资产，有'自出卷'偏差；" % t1)
    lines.append("   故并列留出集 %d 题（本次未修资产 + 反向不瞎编）作为校准。" % t2)
    lines.append("   本卡不引入 LLM judge，答案键均为本机实测事实，无主观空间。")
    lines.append("   对比参考：LoCoMo 答案键约 6.4% 错误、LLM judge 接受约 63% 故意错答。")
    lines.append("-" * 72)
    lines.append("  ⚠ 失真警告：本卡三项均为'本机资产'类问题，且今天刚被补齐；")
    lines.append("    满分 ≠ 中枢通用水平好。它只证明'资产类'这一维合格。")
    lines.append("    真正的通用能力（跨会话长程推理、时序、知识更新、矛盾消解）")
    lines.append("    本卡不测——那需要 LoCoMo/LongMemEval/BEAM 数据集，属另一条轨道。")
    lines.append("=" * 72)

    text = "\n".join(lines)
    print(text)

    if "--save" in sys.argv:
        with open(SCORECARD, "w", encoding="utf-8") as fp:
            json.dump(dict(
                ts=dt.datetime.now().isoformat(),
                coverage=[h, t], freshness=[f, ft],
                bench_main=[p1, t1], bench_holdout=[p2, t2],
                score=round(total, 4),
            ), fp, ensure_ascii=False, indent=2)
        print("已存 %s" % SCORECARD)
    return total


if __name__ == "__main__":
    score()
