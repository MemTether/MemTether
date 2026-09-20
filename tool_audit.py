#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tool_audit.py —— 本机工具资产普查 + 校验（可重复运行）

定位：解决"记忆里压根没这条"和"记错了没人发现"两个病。
- audit()  : 扫盘，产出候选资产清单（不写库）
- verify() : 对 tool_assets 每条跑校验，失活即标记 status
- seed()   : 把审计结果写入 tool_assets（upsert，带 verification_method）

用法：
  python tool_audit.py audit     # 只扫，打印
  python tool_audit.py verify    # 校验库内条目
  python tool_audit.py seed      # 写入/更新库
"""
import os
import sys
import json
import sqlite3
import subprocess
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "memory.db")

# ---------------------------------------------------------------- 资产目录
    # 每条：(name, path, kind, entrypoint, verify_cmd, capabilities, notes)
# verify_cmd 是 shell 命令，exit 0 = 存活。全部用 test -f / -d，不依赖外部程序。
# ★原则：entrypoint 必须是实测验证过的，且 seed 每次全字段覆盖，防止旧错误残留。
# ★2026-09-16 开源改造：清单**外置**。
#   本机装了什么是「数据」不是「代码」——写死在源码里既暴露盘符/账户/软件清单，
#   又让别人 clone 后看到一堆不存在的路径（脚本看着能跑、结果全 MISS）。
#   → 搬到 assets.local.json（被 .gitignore 排除，与 local_paths.json 同构）。
#   缺失时 ASSETS 为空，audit()/seed() 会**明确提示「清单未配置」**，而不是静默跑出 0 条。
_ASSETS_F = os.path.join(HERE, "assets.local.json")
try:
    with open(_ASSETS_F, encoding="utf-8") as _f:
        ASSETS = [tuple(x) for x in json.load(_f)]
except (OSError, ValueError):
    ASSETS = []


def audit():
    """扫盘 + 报告哪些资产没入库"""
    if not ASSETS:
        print("资产清单未配置：请在 %s 放一份（见 README「本机路径外置」）。" % _ASSETS_F)
        return
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT name, path FROM tool_assets")
    known = {}
    for n, p in cur.fetchall():
        known[n] = p
    conn.close()

    print(f"{'状态':<6} {'名称':<24} {'路径'}")
    print("-" * 100)
    miss, ok = [], []
    for a in ASSETS:
        name, path = a[0], a[1]
        exists = os.path.exists(path.replace("/", os.sep))
        tag = "OK" if exists else "MISS"
        if exists:
            ok.append(name)
        else:
            miss.append((name, path))
        print(f"{tag:<6} {name:<24} {path}")
    print("-" * 100)
    print(f"活 {len(ok)} / 缺 {len(miss)}")
    for n, p in miss:
        print(f"  缺: {n} -> {p}")

    new = [n for n, *_ in ASSETS if n not in known]
    print(f"\n库内已有 {len(known)} 条，本次候选 {len(ASSETS)} 条，新增 {len(new)} 条：")
    for n in new:
        print("  +", n)
    stale = [n for n in known if n not in [a[0] for a in ASSETS]]
    if stale:
        print(f"库内有但本次未覆盖 {len(stale)} 条：{stale}")


def _run_verify(cmd: str, path: str) -> bool:
    """校验。支持 test -f / test -d / test -e，纯 Python 实现，不依赖外部 shell。"""
    import re
    m = re.match(r'\s*test\s+(-[fde])\s+"?([^"]+)"?\s*$', cmd or "")
    if m:
        flag, target = m.group(1), m.group(2).replace("/", os.sep)
        if flag == "-f":
            return os.path.isfile(target)
        if flag == "-d":
            return os.path.isdir(target)
        return os.path.exists(target)
    # 未知命令：退化为路径存在性
    return os.path.exists((path or "").replace("/", os.sep))


def verify():
    """对库内每条跑校验，更新 last_verified_at / status"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, name, path, verification_method FROM tool_assets ORDER BY id")
    rows = cur.fetchall()
    print(f"{'结果':<6} {'ID':<4} {'名称':<22} 校验")
    print("-" * 88)
    good = bad = 0
    results = []
    for r in rows:
        vm = r["verification_method"] or f'test -e "{r["path"].replace(chr(92), "/")}"'
        alive = _run_verify(vm, r["path"])
        if alive:
            good += 1
        else:
            bad += 1
        results.append((r["id"], alive, vm, r["name"]))
        print(f"{'OK' if alive else 'DEAD':<6} {r['id']:<4} {r['name']:<22} {vm}")
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for rid, alive, vm, _ in results:
        cur.execute(
            "UPDATE tool_assets SET last_verified_at=?, verification_method=?, "
            "status=?, updated_at=? WHERE id=?",
            (now, vm, "active" if alive else "missing", now, rid),
        )
    conn.commit()
    print("-" * 88)
    print(f"存活 {good} / 失活 {bad}  （校验时间 {now}）")
    conn.close()
    return good, bad


def seed():
    """把 ASSETS 写入 tool_assets（upsert by name）"""
    if not ASSETS:
        print("资产清单未配置：请在 %s 放一份，本次未写入任何条目。" % _ASSETS_F)
        return
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    added = updated = 0
    for a in ASSETS:
        # 兼容 (name,path,kind[,entry],verify,caps,notes)
        if len(a) == 7:
            name, path, kind, entry, ver, caps, notes = a
        else:
            name, path, kind, ver, caps, notes = a
            entry = ""
        exists = os.path.exists(path.replace("/", os.sep))
        cur.execute("SELECT id FROM tool_assets WHERE name=?", (name,))
        row = cur.fetchone()
        status = "active" if exists else "missing"
        if row:
            cur.execute(
                """UPDATE tool_assets SET path=?, type=?, entrypoint=?,
                   capabilities=?, prerequisites=?, verification_method=?,
                   last_verified_at=?, status=?, updated_at=?
                   WHERE id=?""",
                (path, kind, entry, caps, notes, ver, now, status, now, row[0]),
            )
            updated += 1
        else:
            uid = "tool-" + dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-" + os.urandom(6).hex()
            cur.execute(
                """INSERT INTO tool_assets
                   (uid,name,aliases,type,status,path,entrypoint,capabilities,
                    known_failures,prerequisites,last_verified_at,verification_method,
                    source,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid, name, name, kind, status, path,
                 entry, caps, "[]", notes, now, ver, "tool_audit.py", now, now),
            )
            added += 1
    conn.commit()
    print(f"新增 {added} 条，更新 {updated} 条，共 {added+updated}")
    conn.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if mode == "audit":
        audit()
    elif mode == "verify":
        verify()
    elif mode == "seed":
        seed()
    else:
        print(__doc__)
