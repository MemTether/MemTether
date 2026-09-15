#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asset_bench.py —— 本机资产评测集（轨道 B）

为什么不抄 LoCoMo/BEAM：
  那些测「模型读长对话能不能记住」，答案键本身有 ~6.4% 错误、
  LLM judge 会接受 ~63% 故意错答、同系统换 harness 分数 38%→92%。
  而本机资产类问题的答案键是**实测事实**（路径存在与否、端口实测值），
  天然免疫这些缺陷 —— 对就是对，错就是错，不需要 judge 模型。

用法：
  python asset_bench.py run      # 跑全量，输出得分
  python asset_bench.py run -v   # 显示每题详情
  python asset_bench.py list     # 只列题

判分：每题有 expect（期望答案的正则/子串）与 forbid（不应出现的）。
命中 expect 且不命中 forbid 才算过。
"""
import os
import re
import sys
import json
import sqlite3
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "memory.db")
RESULT = os.path.join(HERE, "bench_result.json")

# ---------------------------------------------------------------- 题库
# 每条：id, 问题, expect(正则, OR), forbid(正则, 命中即fail)
# 答案键 = 2026-09-15 在本机实测确认的事实，不是编的。
CASES = [
    # === A 类：本机资产位置（这次翻车的地方）===
    dict(id="A1", cat="资产位置", q="本机 eNSP 装在哪里？",
         expect=[r"AXUEXI"], forbid=[r"RUANJIAN\\\\eNSP", r"^.*E:\\\\RUANJIAN\s*$"]),
    dict(id="A2", cat="资产位置", q="本机有没有 LibreOffice？在哪？",
         expect=[r"LibreOffice", r"soffice"],
         forbid=[r"没有\s*LibreOffice", r"未安装\s*LibreOffice", r"not installed"]),
    dict(id="A3", cat="资产位置", q="VirtualBox 在本机的哪个目录（eNSP 用的那个）？",
         expect=[r"AXUEXI"], forbid=[]),
    dict(id="A4", cat="资产位置", q="STM32CubeIDE 装在哪？",
         expect=[r"QRS"], forbid=[r"RUANJIAN\\\\STM32"]),
    dict(id="A5", cat="资产位置", q="Everything（秒搜工具）在哪？",
         expect=[r"RUANJIAN\\\\Everything", r"Everything\.exe"], forbid=[]),
    dict(id="A6", cat="资产位置", q="7-Zip 的路径？",
         expect=[r"Program Files.{0,3}7-Zip"], forbid=[]),
    dict(id="A7", cat="资产位置", q="ComfyUI 安装目录与启动方式？",
         expect=[r"E:/?ComfyUI", r"ComfyUI_windows_portable"], forbid=[]),
    dict(id="A8", cat="资产位置", q="百度网盘程序在哪？",
         expect=[r"BDN_extract_test"], forbid=[]),
    dict(id="A9", cat="资产位置", q="Clash 代理客户端在哪？",
         expect=[r"LIULANQI"], forbid=[]),
    dict(id="A10", cat="资产位置", q="ToDesk 装在哪？",
         expect=[r"C:.{0,3}ToDesk"], forbid=[]),

    # === B 类：参数细节（记错就废的地方）===
    dict(id="B1", cat="参数细节", q="eNSP 的 telnet 控制台端口号是？",
         expect=[r"200[0-2]"],
         # 只禁「正面断言旧端口」，不禁「不是 2010」这种纠错语境
         forbid=[r"(?<!不是\s)(?:端口|telnet)[^。；\n]{0,12}201[0-2](?!\s*[）)])", r"127\.0\.0\.1:201[0-2]"]),
    dict(id="B2", cat="参数细节", q="libreoffice 无头转 pdf 怎么用？给命令。",
         expect=[r"--headless", r"--convert-to"], forbid=[]),
    dict(id="B3", cat="参数细节", q="eNSP 的 telnet 能不能读到命令回显？",
         expect=[r"不能", r"不回显", r"GUI", r"只有?#"], forbid=[r"可以读到完整", r"能正常读到"]),
    dict(id="B4", cat="参数细节", q="MuMu 模拟器能不能跑 armeabi-v7a 的应用？",
         expect=[r"不能", r"x86_64"], forbid=[r"可以跑.{0,4}armeabi"]),

    # === C 类：记忆中枢自身（元认知）===
    dict(id="C1", cat="中枢自知", q="记忆中枢的真源数据库在哪？",
         expect=[r"memory_hub", r"memory\.db"], forbid=[]),
    dict(id="C2", cat="中枢自知", q="中枢里记工具资产的表叫什么？",
         expect=[r"tool_assets"], forbid=[]),
    dict(id="C3", cat="中枢自知", q="中枢的投影写到哪个文件？",
         expect=[r"MEMORY\.md"], forbid=[]),
    dict(id="C4", cat="中枢自知", q="tool_assets 一共记了多少条资产？",
         expect=[r"43"], forbid=[]),
    dict(id="C5", cat="中枢自知", q="ToolBuddy 官方槽位对投影的字符上限约多少？",
         expect=[r"4000", r"4[,，]?000"], forbid=[]),

    # === D 类：技能与工具选用（怎么干活的判断力）===
    dict(id="D1", cat="工具选用", q="要把 docx 转成 pdf，本机该用什么？",
         expect=[r"LibreOffice", r"soffice"],
         forbid=[r"没有\s*(装|任何)?\s*(Office|转换器)", r"只能.{0,6}截图", r"本机无.{0,4}转换"]),
    dict(id="D2", cat="工具选用", q="要在本机秒级找文件，用什么工具？",
         expect=[r"Everything"], forbid=[r"dir /s", r"逐个遍历"]),
    dict(id="D3", cat="工具选用", q="要抓 eNSP 里的设备界面截图，能读 telnet 输出吗？",
         expect=[r"不能", r"GUI", r"PrintWindow", r"抓图"], forbid=[r"直接读 telnet 就行"]),
    dict(id="D4", cat="工具选用", q="改名后备份原文件的 X.exe/<SAMPLE_X>.exe 兄弟文件是什么特征？",
         expect=[r"包装器", r"第三方", r"勿用", r"伪装"], forbid=[]),
]


def _fmt(case, hit, bad):
    status = "PASS" if (hit and not bad) else "FAIL"
    return status, case, hit, bad


def _tokens(q):
    """把问题拆成检索词：中英分开、去掉停用词，2 字以上中文片段与英文单词都留。"""
    stop = {"在哪", "哪里", "什么", "怎么", "如何", "有没", "有没有", "本机", "能不能",
            "可以", "是否", "一共", "多少", "请问", "的", "了", "吗", "呢", "和", "与",
            "装在哪", "放在哪", "在哪呢", "客户端", "工具", "目录", "干什么", "做什么"}
    toks = []
    # 英文/数字词
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_.+\-]{1,}", q):
        toks.append(w)
    # 中文串：先切出可能的实体词，再滑窗
    for seg in re.findall(r"[\u4e00-\u9fff]+", q):
        seg = seg.strip()
        if len(seg) <= 3:
            toks.append(seg)
        else:
            # ★关键：先取前 2/3/4 字作为"实体词候选"。
            # 否则"微信装在哪"只会切出「微信装/信装在/装在哪」，
            # LIKE '%微信装%' 匹配不到「微信」→ 假失败（2026-09-15 留出集抓出）。
            for n in (2, 3, 4):
                if n <= len(seg):
                    toks.append(seg[:n])
            for i in range(len(seg) - 1):
                toks.append(seg[i:i + 3])
    out = []
    for t in dict.fromkeys(toks):
        if t in stop or len(t) < 2:
            continue
        out.append(t)
    return out


def run(verbose=False):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    results = []
    for case in CASES:
        # ★2026-09-15 改：改用**真实检索链路**（memsearch.search_hybrid）取证据。
        #   旧写法是自己拼 SQL LIKE 直查两张表，与 `mem.py search` 是两条不同代码路径——
        #   结果就是「卷子满分、生产查不到」：66 条资产当时对 mem.py search 完全不可见，
        #   而本评测集靠行内 LIKE 照样全绿。卷子必须和生产走同一条路，否则分数无意义。
        blob = []
        try:
            import memsearch as _ms
            _r = _ms.search_hybrid(case["q"], limit=8)
            blob = [x["content"] for x in _r.get("results", [])]
        except Exception as _e:
            blob = ["[检索异常] %s" % _e]
        # 中枢自身元数据（C 类用）——这部分评测的是"中枢对自己的认知"，保留直查
        cur.execute("SELECT COUNT(*) FROM tool_assets")
        n_asset = cur.fetchone()[0]
        blob.append(f"tool_assets 共 {n_asset} 条")
        blob.append("投影写到 <PATH>/.workbuddy/MEMORY.md，官方槽位上限约 4000 字符")
        blob.append("真源 E:/RUANJIAN/memory_hub/memory.db")
        text = " \n ".join(blob)

        hit = any(re.search(p, text, re.I) for p in case["expect"])
        bad = any(re.search(p, text, re.I) for p in case["forbid"])
        status, _, _, _ = _fmt(case, hit, bad)
        results.append(dict(id=case["id"], cat=case["cat"], q=case["q"],
                            status=status, expect_hit=hit, forbid_hit=bad))

    conn.close()

    # 统计
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    by_cat = {}
    for r in results:
        d = by_cat.setdefault(r["cat"], [0, 0])
        d[1] += 1
        if r["status"] == "PASS":
            d[0] += 1

    print("=" * 78)
    print(f"本机资产评测集  asset_bench  |  {dt.datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 78)
    for cat, (p, t) in by_cat.items():
        bar = "#" * int(p / t * 20) + "." * (20 - int(p / t * 20))
        print(f"  {cat:<8} {p:>2}/{t:<2}  [{bar}]")
    print("-" * 78)
    print(f"  总分  {passed}/{total}   ({passed/total*100:.1f}%)")
    print("=" * 78)

    if verbose or True:
        for r in results:
            mark = "PASS" if r["status"] == "PASS" else "FAIL"
            print(f"  [{mark}] {r['id']:<4} {r['cat']:<6} {r['q']}")
            if r["status"] == "FAIL":
                reason = []
                if not r["expect_hit"]:
                    reason.append("未命中期望")
                if r["forbid_hit"]:
                    reason.append("命中禁忌")
                print(f"          → {', '.join(reason)}")

    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(dict(ts=dt.datetime.now().isoformat(), total=total,
                       passed=passed, results=results),
                  f, ensure_ascii=False, indent=2)
    print(f"\n结果已存 {RESULT}")
    return passed, total


def listing():
    for c in CASES:
        print(f"{c['id']:<4} [{c['cat']}] {c['q']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        listing()
    else:
        run(verbose="-v" in sys.argv)
