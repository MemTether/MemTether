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
    dict(id="R3", cat="反向-不瞎编", q="本机装了 Adobe Premiere 吗？",
         expect=[r"没有|未安装|不(存在|在)|查无|未收录|absent|not found"],
         forbid=[]),

    # === C 盘资产（2026-09-15 第二轮普查新增，同样未"修过"）===
    dict(id="K1", cat="C盘资产", q="Microsoft Edge 在哪？怎么用它把 HTML 转 PDF？",
         expect=[r"msedge\.exe|Edge"], forbid=[]),
    dict(id="K2", cat="C盘资产", q="Visual Studio Code 装在哪？",
         expect=[r"VS Code|Code\.exe"], forbid=[]),
    dict(id="K3", cat="C盘资产", q="本机 winget 能用吗？在哪？",
         expect=[r"winget"], forbid=[]),
    dict(id="K4", cat="C盘资产", q="Cheat Engine 装在哪？",
         expect=[r"Cheat Engine"], forbid=[]),
    dict(id="K5", cat="C盘资产", q="Npcap 是干什么的？",
         expect=[r"Npcap|抓包"], forbid=[]),
    dict(id="K6", cat="C盘资产", q="WCHISPTool 在哪？做什么的？",
         expect=[r"WCH|isp|烧录"], forbid=[]),
    dict(id="K7", cat="C盘资产", q="DrvCeo 是什么？在哪？",
         expect=[r"DrvCeo|驱动"], forbid=[]),
    dict(id="K8", cat="C盘资产", q="微信装在哪？",
         expect=[r"WEIXIN|Weixin"], forbid=[]),
    dict(id="K9", cat="C盘资产", q="豆包客户端在哪？",
         expect=[r"Doubao"], forbid=[]),
    dict(id="K10", cat="C盘资产", q="迅雷装在哪？",
         expect=[r"Thunder"], forbid=[]),
    dict(id="K11", cat="C盘资产", q="网易云音乐在哪？",
         expect=[r"CloudMusic|cloudmusic"], forbid=[]),
    dict(id="K12", cat="C盘资产", q="本机有哪些压缩解压工具？",
         expect=[r"7-Zip|NanaZip|Bandizip|WinRAR"], forbid=[]),
    dict(id="K13", cat="C盘资产", q="Discord 装了吗？在哪？",
         expect=[r"Discord"], forbid=[]),
    dict(id="K14", cat="C盘资产", q="AntiCheatExpert 是什么？",
         expect=[r"AntiCheat|反作弊|ACE"], forbid=[]),

    # === S 系列：2026-09-15 修「资产检索缺口」后新加的题 ===
    # 这组专测刚修的那条链路——资产能否被 mem.py search 检索到（此前 100% 检索不到）。
    # 注意：它们在修复**之后**才加入，属"自己出的卷子"，只作回归用，
    # 不能拿它们的满分去论证系统水平（见 hub_score.py 的失真声明）。
    dict(id="S1", cat="检索缺口回归", q="mcp_server.py 在哪？",
         expect=[r"memory_hub"], forbid=[r"没有收录|未收录|查无"]),
    dict(id="S2", cat="检索缺口回归", q="记忆中枢的检索要用哪个 python？",
         expect=[r"venv-memory"], forbid=[r"没有收录|不知道|无法确定"]),
    dict(id="S3", cat="检索缺口回归", q="eNSP 的 telnet 端口是多少？",
         expect=[r"200[0-2]"],
         # forbid：只在「把 2010/2011/2012 当答案」时才判负。
         # ★踩坑记录：本机正确条目原文是「…端口是 2000/2001/2002…，不是 2010/2011/2012」。
         #   用 (?<!不是\s)201[0-2] 做负向后查**无效**——因为 2011/2012 前面是斜杠不是「不是 」，
         #   只有 2010 能躲过，后两个照样命中 → 把正确答案判成 FAIL（同 B1 的坑）。
         #   定宽后查表达不了「整串被否定」，改用：存在 2010/2011/2012 且该处**没有**被「不是/非/旧」否定。
         forbid=[r"(?<!不是\s)(?<!非)(?:端口|telnet)[^。；\n]{0,12}201[0-2](?!\s*[）)])"]),
    dict(id="S4", cat="检索缺口回归", q="本机有 LibreOffice 吗？在哪？",
         expect=[r"LibreOffice"], forbid=[r"没有\s*LibreOffice|未安装\s*LibreOffice"]),
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
            # ★2026-09-15 改：改用**真实检索链路**（memsearch.search_hybrid）取证据，
            #   而不是行内 LIKE 直查 tool_assets 表。原因：
            #   (a) 旧写法只 SELECT name||path||capabilities||prerequisites，**漏掉 entrypoint**，
            #       而「mcp_server.py 在哪」的答案恰好写在 entrypoint 里 → S1 误 FAIL；
            #   (b) 旧写法的分词用 asset_bench._tokens 拼 LIKE，**测的是 SQL 子串匹配**，
            #       与实际 `mem.py search` 走的是两条不同代码路径 —— 卷子和生产不一致，
            #       这种"自出卷子"的分数再高也不代表用户真问的时候能查到。
            #   现在统一走 search_hybrid：离线可跑（chromadb 在 .venv-memory），且与生产同路径。
            try:
                import memsearch as _ms
                _r = _ms.search_hybrid(case["q"], limit=8)
                blob = [x["content"] for x in _r.get("results", [])]
            except Exception as _e:
                blob = ["[检索异常] %s" % _e]
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
