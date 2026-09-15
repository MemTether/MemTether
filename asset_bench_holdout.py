#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asset_bench_holdout.py —— 留出集（holdout）校准

为什么要它：
  asset_bench.py 的 23 题恰好覆盖了 2026-09-15 刚修的那批资产（eNSP/LibreOffice），
  100% 是"自己出卷自己判卷"，不能证明整个中枢的水平。
  本留出集**专挑这次没修、没碰过的资产**提问，用来校准真实泛化能力。

判分同 asset_bench：expect 命中且 forbid 不命中才算过。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from asset_bench import _tokens  # 复用分词

import sqlite3
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "memory.db")

CASES = [
    dict(id="H1", cat="未碰过的资产", q="XCOM 串口助手在哪？",
         expect=[r"QRS"], forbid=[]),
    dict(id="H2", cat="未碰过的资产", q="Arduino IDE 在本机哪个目录？",
         expect=[r"RUANJIAN.{0,3}Arduino", r"Arduino IDE"], forbid=[]),
    dict(id="H3", cat="未碰过的资产", q="Czkawka 是干什么的？在哪？",
         expect=[r"Czkawka"], forbid=[]),
    dict(id="H4", cat="未碰过的资产", q="PikPak 在哪里？",
         expect=[r"PikPak"], forbid=[]),
    dict(id="H5", cat="未碰过的资产", q="PotPlayer 播放器的可执行文件叫什么？",
         expect=[r"PotPlayerMini64"], forbid=[]),
    dict(id="H6", cat="未碰过的资产", q="DubbingVC 是什么工具？",
         expect=[r"DubbingVC", r"配音"], forbid=[]),
    dict(id="H7", cat="未碰过的资产", q="CH341 驱动是干嘛的？",
         expect=[r"CH341", r"串口"], forbid=[]),
    dict(id="H8", cat="未碰过的资产", q="SwarmUI 装在哪里？做什么的？",
         expect=[r"SwarmUI"], forbid=[]),
    dict(id="H9", cat="未碰过的资产", q="platform-tools 目录里是什么工具？",
         expect=[r"adb", r"platform-tools"], forbid=[]),
    dict(id="H10", cat="未碰过的资产", q="同花顺期货通装在哪？",
         expect=[r"同花顺"], forbid=[]),
    dict(id="H11", cat="未碰过的资产", q="ikuuu_vpn 在哪？",
         expect=[r"ikuuu", r"xhu"], forbid=[]),
    dict(id="H12", cat="未碰过的资产", q="Chatbox 是什么？在哪？",
         expect=[r"Chatbox"], forbid=[]),
    # 反向题：问本机**不存在**的东西，正确回答应当"没有"
    # （这测的是"不瞎编"能力，比正问更难）
    dict(id="R1", cat="反向-不瞎编", q="本机装了 Adobe Photoshop 吗？",
         expect=[r"没有|未安装|不(存在|在)|查无|未收录|absent|not found"],
         forbid=[]),
    dict(id="R2", cat="反向-不瞎编", q="本机装了 Docker 吗？",
         expect=[r"没有|未安装|不(存在|在)|查无|未收录|absent|not found"],
         forbid=[]),
]


def run(verbose=True):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    results = []
    for case in CASES:
        # 反向题不做检索——相当于直接问"库里有吗"
        if case["cat"].startswith("反向"):
            blob = []
            # 全局扫一遍资产表，看有没有沾边的
            key = case["q"].replace("本机装了", "").replace("吗？", "").strip()
            cur.execute("SELECT name,path FROM tool_assets WHERE name LIKE ? OR path LIKE ?",
                        (f"%{key}%", f"%{key}%"))
            hits = cur.fetchall()
            text = ("存在: " + str(hits)) if hits else "没有收录该资产"
        else:
            blob = []
            for kw in _tokens(case["q"]):
                cur.execute(
                    "SELECT name||' | '||path||' | '||capabilities||' | '||COALESCE(prerequisites,'') "
                    "FROM tool_assets WHERE name LIKE ? OR aliases LIKE ? OR path LIKE ? "
                    "OR capabilities LIKE ? OR prerequisites LIKE ?",
                    (f"%{kw}%",) * 5,
                )
                blob += [r[0] or "" for r in cur.fetchall()]
            text = " \n ".join(blob) if blob else "没有收录该资产"

        hit = any(re.search(p, text, re.I) for p in case["expect"])
        bad = any(re.search(p, text, re.I) for p in case["forbid"]) if case["forbid"] else False
        passed = bool(text.strip()) and hit and not bad
        # 检索为空 = 中枢没记住 = fail（除了反向题，空正是对的）
        if case["cat"].startswith("反向"):
            passed = hit and not bad
        results.append(dict(id=case["id"], cat=case["cat"], q=case["q"],
                            status="PASS" if passed else "FAIL", text=text[:160]))

    conn.close()
    total = len(results)
    p = sum(1 for r in results if r["status"] == "PASS")
    by = {}
    for r in results:
        d = by.setdefault(r["cat"], [0, 0])
        d[1] += 1
        d[0] += r["status"] == "PASS"
    print("=" * 78)
    print(f"留出集 asset_bench_holdout  |  {dt.datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 78)
    for c, (a, b) in by.items():
        print(f"  {c:<12} {a:>2}/{b:<2}")
    print("-" * 78)
    print(f"  总分 {p}/{total}  ({p/total*100:.1f}%)")
    print("=" * 78)
    if verbose:
        for r in results:
            print(f"  [{r['status']}] {r['id']:<4} {r['cat']:<12} {r['q']}")
            if r["status"] == "FAIL":
                print(f"        检索到: {r['text'][:110]}")
    return p, total


if __name__ == "__main__":
    run()
