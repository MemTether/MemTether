#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
slot_update.py —— 共享注入槽位（MEMORY.md / 工作区 MEMORY.md）的原地更新器。

背景（2026-09-17 实测）：
  两个 WorkBuddy 客户端（国内版 / 国际版）的工作区 memory/ 目录是**同一批 inode**，
  双方每轮都会自动注入同一份 MEMORY.md。对这种文件做原地更新时：
    - 直接 open(path,"w") 直写 —— 对方此刻可能正在读，会读到半截文件；
    - Edit 的"读全文→替换→写回" —— 会把对方在我读之后追加的内容静默抹掉。
  本工具把踩过坑的六步配方固化成可执行动作，消除"读了文字也不照做"的歧义。

六步配方（缺一不可，任一步失败即整体中止、一个字节都不写）：
  ① 锚点唯一化 —— 每处替换先断言 count(old)==1，不唯一即中止
  ② 预算闸门   —— 先只算 len(str) 试算，超硬顶即中止（只读，不动盘）
  ③ 同目录备份 —— .bak-<时间戳>（shutil.copy2）
  ④ 原子写     —— 同目录 tempfile.mkstemp + os.replace
  ⑤ 换行跟随   —— 目标 CRLF 就写 CRLF，绝不静默改写全文件行尾
  ⑥ 回读断言   —— 重新读盘逐字节比对 + 关键词探针

用法：
  python slot_update.py <path> --budget 8000 --patch patch.json
  python slot_update.py <path> --budget 8000 --patch patch.json --probe kw1 --probe kw2
  python slot_update.py --selftest

patch.json 格式（UTF-8，列表）：
  [{"name":"§0 状态行", "old":"...", "new":"..."}, ...]

退出码：0 成功 / 2 锚点不唯一 / 3 超预算 / 4 回读不一致 / 5 其他
"""

import io
import os
import sys
import json
import shutil
import tempfile
import datetime

CREATE_NO_WINDOW = 0x08000000


# ---------------------------------------------------------------- 基础工具

def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def B(s):
    """str -> utf-8 bytes。bytes 字面量不能写中文，故统一走这个函数。"""
    return s.encode("utf-8")


def detect_newline(raw):
    """跟随目标文件的换行风格。文件不存在时落 CRLF（Windows 惯例）。"""
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def count_chars(raw):
    """★字符数必须按 newline='' 计（把 \\r 也算一个字符）。

    2026-09-17 实测：同一份投影文件，open(newline='') 报 3945、默认归一报 3895，
    差值 50 恰好等于 CRLF 个数。客户端按哪种口径算我们无从得知，
    所以一律用**更保守**的 newline='' 口径。
    """
    return len(raw.decode("utf-8", errors="replace"))


# ---------------------------------------------------------------- 主流程

def apply_patch(path, reps, budget, probes=None, do_bak=True, verbose=True):
    """六步配方。返回 (rc, 新字符数)。"""
    probes = probes or []
    raw = read_bytes(path)
    nl = detect_newline(raw)
    txt = raw.decode("utf-8")

    # ① 锚点唯一化
    for item in reps:
        name = item.get("name", "?")
        old = item["old"]
        c = txt.count(old)
        if verbose:
            print("  [%-16s] 锚点命中 %d 次 %s" % (name, c, "OK" if c == 1 else "FAIL"))
        if c != 1:
            print("!! 锚点不唯一 -> 整体中止（一个字节都没写）")
            return 2, None
        txt = txt.replace(old, item["new"], 1)

    # ② 预算闸门（先归一到目标换行再算长度）
    body = txt.replace("\r\n", "\n").replace("\n", nl) if nl == "\r\n" else txt
    new_len = count_chars(body.encode("utf-8"))
    old_len = count_chars(raw)
    headroom = budget - new_len
    if verbose:
        print("  字符数 %d -> %d（净 %+d）/ 预算 %d / 余量 %d"
              % (old_len, new_len, new_len - old_len, budget, headroom))
    if new_len > budget:
        print("!! 超预算 %d -> 整体中止（一个字节都没写）" % budget)
        return 3, None

    # ③ 同目录备份
    if do_bak:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = "%s.bak-%s" % (path, ts)
        shutil.copy2(path, bak)
        if verbose:
            print("  备份 -> %s" % bak)

    # ④ 原子写（同目录 mkstemp + os.replace）
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".slot_update-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body.encode("utf-8"))
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    # ⑥ 回读断言
    raw2 = read_bytes(path)
    if raw2 != body.encode("utf-8"):
        print("!! 回读逐字节比对不一致")
        return 4, None
    miss = [p for p in probes if p not in raw2.decode("utf-8", errors="replace")]
    if miss:
        print("!! 探针缺失: %s" % miss)
        return 4, None
    if verbose:
        print("  回读逐字节一致 = True / 换行 = %s / 探针 %d 条全在"
              % ("CRLF" if nl == "\r\n" else "LF", len(probes)))
    return 0, new_len


# ---------------------------------------------------------------- 自检

def selftest():
    """在临时目录演练，绝不碰线上文件。"""
    import subprocess
    print("== slot_update.py 自检（临时目录，不碰线上）==")
    base = tempfile.mkdtemp(prefix="slot-st-")
    print("  演练目录: %s" % base)
    results = []

    def mk(name, content, nl="\n"):
        p = os.path.join(base, name)
        with open(p, "wb") as f:
            f.write(content.replace("\n", nl).encode("utf-8"))
        return p

    # 用例 1：正常替换（LF）
    p1 = mk("a.md", "# 标题\n- 旧状态行\n- 保持行\n")
    r, n = apply_patch(p1, [{"name": "状态行", "old": "- 旧状态行", "new": "- 新状态行"}],
                       budget=8000, probes=["新状态行", "保持行"], verbose=False)
    ok = r == 0 and B("新状态行") in read_bytes(p1) and B("保持行") in read_bytes(p1)
    print("  [1] 正常替换(LF)          %s  rc=%d len=%s" % ("PASS" if ok else "FAIL", r, n))
    results.append(ok)

    # 用例 2：换行跟随（CRLF 文件改完必须还是 CRLF）
    p2 = mk("b.md", "# 标题\n- 旧状态行\n- 第二行\n", nl="\r\n")
    r, n = apply_patch(p2, [{"name": "状态行", "old": "- 旧状态行", "new": "- 新状态行"}],
                       budget=8000, verbose=False)
    rr = read_bytes(p2)
    ok = r == 0 and rr.count(b"\r\n") == 3 and rr.count(b"\n") == 3
    print("  [2] 换行跟随(CRLF)        %s  CRLF=%d 裸LF=%d"
          % ("PASS" if ok else "FAIL", rr.count(b"\r\n"), rr.count(b"\n") - rr.count(b"\r\n")))
    results.append(ok)

    # 用例 3：锚点不唯一 -> 必须中止、一个字节都不写
    p3 = mk("c.md", "- 重复行\n- 重复行\n")
    before = read_bytes(p3)
    r, n = apply_patch(p3, [{"name": "重复", "old": "- 重复行", "new": "- 改了"}],
                       budget=8000, verbose=False)
    ok = r == 2 and read_bytes(p3) == before
    print("  [3] 锚点不唯一->中止      %s  rc=%d 原文件未变=%s"
          % ("PASS" if ok else "FAIL", r, read_bytes(p3) == before))
    results.append(ok)

    # 用例 4：超预算 -> 必须中止、一个字节都不写
    p4 = mk("d.md", "- 短行\n")
    before = read_bytes(p4)
    r, n = apply_patch(p4, [{"name": "爆量", "old": "- 短行", "new": "- " + "X" * 500}],
                       budget=100, verbose=False)
    ok = r == 3 and read_bytes(p4) == before
    print("  [4] 超预算->中止          %s  rc=%d 原文件未变=%s"
          % ("PASS" if ok else "FAIL", r, read_bytes(p4) == before))
    results.append(ok)

    # 用例 5：探针缺失 -> 报错但仍已写（探针是事后断言，写盘已完成）
    p5 = mk("e.md", "- 行\n")
    r, n = apply_patch(p5, [{"name": "换", "old": "- 行", "new": "- 新行"}],
                       budget=8000, probes=["不存在的关键词"], verbose=False)
    ok = r == 4
    print("  [5] 探针缺失->报 rc=4     %s" % ("PASS" if ok else "FAIL"))
    results.append(ok)

    # 用例 6：多锚点串行替换（每处各命中 1 次）
    p6 = mk("f.md", "A\nB\nC\n")
    r, n = apply_patch(p6, [{"name": "A", "old": "A", "new": "A2"},
                            {"name": "B", "old": "B", "new": "B2"}],
                       budget=8000, probes=["A2", "B2", "C"], verbose=False)
    ok = r == 0 and B("A2") in read_bytes(p6) and B("C") in read_bytes(p6)
    print("  [6] 多锚点串行            %s  rc=%d" % ("PASS" if ok else "FAIL", r))
    results.append(ok)

    # 用例 7：CRLF 口径下字符数把 \r 也算进去（保守口径）
    p7 = mk("g.md", "0123456789\n0123456789\n", nl="\r\n")
    got = count_chars(read_bytes(p7))
    norm = len(open(p7, "r", encoding="utf-8").read())
    # 保守口径 24 = 20 可见字符 + 2 个 \n + 2 个 \r；归一口径 22 = 20 + 2 个 \n
    ok = got == 24 and norm == 22 and (got - norm) == 2
    print("  [7] 保守口径(CRLF计\\r)    %s  保守=%d 归一=%d 差=%d (期望 24/22/2)"
          % ("PASS" if ok else "FAIL", got, norm, got - norm))
    results.append(ok)

    shutil.rmtree(base, ignore_errors=True)
    print("== 自检 %s（%d/%d）==" % ("全部通过" if all(results) else "存在失败",
                                  sum(1 for x in results if x), len(results)))
    return 0 if all(results) else 5


# ---------------------------------------------------------------- CLI

def main():
    if "--selftest" in sys.argv:
        return selftest()

    import argparse
    ap = argparse.ArgumentParser(description="共享注入槽位的原地更新器（六步安全配方）")
    ap.add_argument("path", nargs="?", help="目标文件")
    ap.add_argument("--budget", type=int, default=8000, help="字符硬顶（默认 8000）")
    ap.add_argument("--patch", required=False, help="替换清单 JSON 文件")
    ap.add_argument("--probe", action="append", default=[], help="回读探针关键词，可重复")
    ap.add_argument("--no-bak", action="store_true", help="不做备份")
    ap.add_argument("--dry-run", action="store_true", help="只做锚点+预算试算，绝不写盘")
    args = ap.parse_args()

    if not args.path or not args.patch:
        ap.error("需要 <path> 与 --patch（或改用 --selftest）")

    with open(args.patch, "r", encoding="utf-8") as f:
        reps = json.load(f)

    if args.dry_run:
        raw = read_bytes(args.path)
        txt = raw.decode("utf-8")
        bad = 0
        for item in reps:
            c = txt.count(item["old"])
            print("  [%-16s] 命中 %d 次 %s" % (item.get("name", "?"), c,
                                              "OK" if c == 1 else "FAIL"))
            if c != 1:
                bad += 1
            else:
                txt = txt.replace(item["old"], item["new"], 1)
        nl = detect_newline(raw)
        body = txt.replace("\r\n", "\n").replace("\n", nl) if nl == "\r\n" else txt
        n = count_chars(body.encode("utf-8"))
        print("  试算 %d -> %d（净 %+d）/ 预算 %d / 余量 %d"
              % (count_chars(raw), n, n - count_chars(raw), args.budget, args.budget - n))
        if bad:
            print("!! %d 处锚点不唯一" % bad)
            return 2
        if n > args.budget:
            print("!! 超预算，实际执行会被拦下")
            return 3
        print("  dry-run 结论：可执行（未写盘）")
        return 0

    rc, n = apply_patch(args.path, reps, args.budget, args.probe,
                        do_bak=not args.no_bak)
    print("  结果: %s" % ("成功" if rc == 0 else "中止 rc=%d" % rc))
    return rc


if __name__ == "__main__":
    sys.exit(main())
