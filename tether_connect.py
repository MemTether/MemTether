#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tether_connect.py — 把记忆中枢「自动接入」到本机所有 AI 客户端

解决的问题
----------
在此之前，每接一个新客户端都要**人工**做四件事（读文档 → 找配置路径 → 试 schema →
写文件 → 再手工授信），每接一家都要重新踩一遍坑。本工具把它变成一条命令。

    python tether_connect.py detect      # 看本机装了哪些客户端、当前接入到什么程度
    python tether_connect.py plan        # 预演：将要改哪些文件、改什么（不写盘）
    python tether_connect.py apply       # 落盘（自动备份 + 幂等）
    python tether_connect.py verify      # 回读校验 + 信任状态 + 在线探测
    python tether_connect.py rollback    # 从备份还原

设计原则
--------
1. **只读优先**：`detect` / `plan` / `verify` 绝不写盘。
2. **幂等**：已装好且一致 → `noop`，不产生写盘（跑一百次结果一样）。
3. **可回滚**：任何写盘前备份到 `~/.memtether/backups/<时间戳>/`，并记 manifest。
4. **失败关闭**：解析不了 / schema 不认识 → 报错跳过，**绝不猜着写**。
5. **保住用户配置**：走 JSONC 外科式编辑，注释、键序、缩进、换行风格都不动。

★「接入」为什么不止是写 MCP 配置
--------------------------------
实测发现 MCP 只是**加成**，不是底线。真正决定「它能不能读写记忆」的是三层：

| 层 | 作用 | 缺了会怎样 |
|---|---|---|
| **技能层** `~/.agents/skills` | 告诉 agent「中枢在哪、怎么查怎么写」 | 有工具也不会用 / 不知道要带 `--source` |
| **来源注册** `agents.json` | 让写入归属正确 | 写进去的结论记到别人头上 |
| **MCP / CLI** | 实际读写通道 | 只能靠 agent 自己想办法 |

所以本工具把三层一起处理：技能层只**检查**（它是共享目录，不该被这个工具覆盖），
来源注册**自动补齐**，MCP 配置**按各家 schema 写入并授信**。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from clients import all_adapters, find as find_adapter, jsonc  # noqa: E402
from clients.base import ServerSpec, Target, Change, BACKUP_ROOT  # noqa: E402

CREATE_NO_WINDOW = 0x08000000


# ==========================================================================
# 服务器规格
# ==========================================================================
def hub_defaults(hub_dir):
    """从记忆中枢目录推导 (python, mcp_server)。

    ★这一步是整个工具最容易出错的地方：如果推导出的 spec 和用户配置里已有的
    不一致，`verify` 会永远报「回读内容不一致」，`apply` 还会把正确配置改错。
    所以优先用中枢自己的 venv 解释器（里面才有 chromadb + onnxruntime，
    否则检索会静默降级成纯关键词），并确认文件真的存在。
    """
    if not hub_dir:
        return None, None
    hub = os.path.abspath(hub_dir)
    mcp = os.path.join(hub, "mcp_server.py")
    py = None
    for rel in (os.path.join(".venv-memory", "Scripts", "python.exe"),   # Windows
                os.path.join(".venv-memory", "bin", "python")):         # POSIX
        cand = os.path.join(hub, rel)
        if os.path.isfile(cand):
            py = cand
            break
    return py, (mcp if os.path.isfile(mcp) else None)


def default_spec(args) -> ServerSpec:
    hub_py, hub_mcp = hub_defaults(getattr(args, "hub_dir", None))
    py = args.python or hub_py or sys.executable
    mcp = args.mcp_server or hub_mcp or os.path.join(HERE, "mcp_server.py")
    return ServerSpec(
        name=args.name,
        command=py,
        args=[mcp] + list(args.arg or []),
        env=dict(kv.split("=", 1) for kv in (args.env or [])),
        transport="stdio",
    )


# ==========================================================================
# 探测
# ==========================================================================
def detect(adapters, spec, verbose=True):
    """返回 [(adapter, Target, installed)]。"""
    out = []
    for a in adapters:
        for scope, path in a.candidates():
            try:
                installed, reason = a.probe(scope, path)
            except Exception as e:
                installed, reason = False, "探测异常: %s" % e
            t = Target(a.id, a.display, scope, path, os.path.exists(path), installed, reason)
            out.append((a, t))
    if verbose:
        print("=" * 78)
        print("探测结果（%d 个适配器 / %d 个落点）" % (len(adapters), len(out)))
        print("=" * 78)
        inst = [(a, t) for a, t in out if t.installed]
        if not inst:
            print("  没有探测到任何已安装的客户端。")
        for a, t in inst:
            mark = "有配置" if t.exists else "无配置(可创建)"
            state = ""
            if t.exists:
                try:
                    st = a.read_back(t, spec)
                    state = "→ %s" % ("已接入" if st[0] else "未接入/不一致")
                except Exception:
                    state = "→ 读取异常"
            print("  %-24s %-22s %-16s %s" % (a.id, t.scope, mark, state))
            print("       %s" % t.path)
    return out


# ==========================================================================
# 计划 / 执行
# ==========================================================================
def do_apply(adapters, spec, dry_run, yes, do_trust=True, hub_dir=None):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    changes = []
    skipped_noinstall = 0
    print("=" * 78)
    print("%s：%d 个客户端 / server=%s" % ("预演（不写盘）" if dry_run else "执行接入",
                                            len(adapters), spec.name))
    print("=" * 78)
    print("  command = %s" % spec.command)
    print("  args    = %s" % json.dumps(spec.args, ensure_ascii=False))
    print()

    for a in adapters:
        cands = a.candidates()
        if not cands:
            print("[%s] 没有可用的配置落点，跳过" % a.id)
            continue
        for scope, path in cands:
            try:
                installed, reason = a.probe(scope, path)
            except Exception as e:
                installed, reason = False, "探测异常: %s" % e
            if not installed:
                skipped_noinstall += 1
                print("[%-22s %-18s] 跳过：%s" % (a.id, scope, reason))
                continue
            t = Target(a.id, a.display, scope, path, os.path.exists(path), True, reason)
            try:
                ch = a.write(t, spec, dry_run=dry_run)
            except Exception as e:
                ch = Change(t, "error", "写入异常: %s" % e)
            changes.append(ch)

            flag = {"noop": "= 已是最新", "insert": "+ 新增", "replace": "~ 更新",
                    "create": "+ 新建", "skip": "- 跳过", "error": "! 失败"}.get(ch.action, ch.action)
            print("[%-22s %-18s] %-10s %s" % (a.id, scope, flag, ch.detail))
            print("       %s" % path)
            if ch.action in ("insert", "replace", "create") and ch.after and not dry_run:
                print("       备份 → %s" % (ch.backup or "（原文件不存在）"))

            # 授信
            if do_trust and not dry_run and ch.action != "error":
                try:
                    res = a.trust(spec, dry_run=False)
                    if res is not None:
                        ok, detail = res
                        print("       %s 信任: %s" % ("√" if ok else "×", detail))
                except Exception as e:
                    print("       × 授信异常: %s" % e)

    # 来源注册
    if hub_dir:
        active = {c.target.client_id for c in changes
                  if c.action in ("create", "insert", "replace", "noop")}
        changes += _register_sources(adapters, hub_dir, dry_run, active_ids=active)

    wrote = [c for c in changes if c.wrote]
    print()
    print("-" * 78)
    print("合计：%d 个落点，其中写入 %d、已是最新 %d、跳过 %d、失败 %d"
          % (len(changes), len(wrote),
             sum(1 for c in changes if c.action == "noop"),
             sum(1 for c in changes if c.action == "skip"),
             sum(1 for c in changes if c.action == "error")))
    if skipped_noinstall:
        # ★单独报：这些是「客户端没装」而不是「工具跳过了」，混在"跳过"里会误导
        print("     另有 %d 个落点因**未发现该客户端安装痕迹**而跳过（不替没装的软件造配置）"
              % skipped_noinstall)
    if not dry_run and wrote:
        from clients.base import write_manifest
        mp = write_manifest(stamp, changes)
        print("备份与 manifest：%s" % mp)
        print("回滚：python tether_connect.py rollback --stamp %s" % stamp)
    if not dry_run:
        # ★只列**真正接入的**（2026-09-20 修）：原先按 adapters 全量列，
        #   于是 23 个客户端全被点名要重启，包括根本没装的 —— 报告全是噪声，
        #   用户根本不知道该动哪个。
        active = {c.target.client_id for c in changes
                  if c.action in ("create", "insert", "replace", "noop")}
        need_restart = sorted({a.id for a in adapters
                               if a.needs_restart and a.id in active})
        if need_restart:
            print()
            print("★ 需要重启客户端才生效：%s" % ", ".join(need_restart))
            print("  （Electron 系若不能重启，可在其 MCP 设置里点一次 Trust 让信任当轮生效）")
    return changes


def _register_sources(adapters, hub_dir, dry_run, active_ids=None):
    """把各客户端的来源名补进 <hub_dir>/agents.json（幂等）。

    ★只给**真正接入的**客户端注册（2026-09-20 实测踩到）：
    原先对全部 23 个适配器都注册，于是 agents.json 里凭空多出 17 个
    根本没装的客户端来源名 —— 而 agents.json 是「归属是否可信」的判据表，
    塞进不存在的东西等于污染它，还会让人以为那些客户端在用中枢。
    active_ids=None 时退回旧行为（全注册），仅供自检使用。
    """
    agents_json = os.path.join(hub_dir, "agents.json")
    changes = []
    if not os.path.isfile(agents_json):
        print("[来源注册] 找不到 %s，跳过" % agents_json)
        return changes
    try:
        with open(agents_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("[来源注册] 解析失败，跳过: %s" % e)
        return changes
    table = data.setdefault("agents", {})
    added = []
    for a in adapters:
        if active_ids is not None and a.id not in active_ids:
            continue
        src = getattr(a, "source", "")
        if not src or src in table:
            continue
        table[src] = {
            "display": a.display,
            "kind": "executor",
            "can_read": True,
            "can_write": True,
            "projection": None,
            "writes_via": "MCP memory-hub（add_memories 带 source=%s）"
                          "或 python gateway.py remember \"...\" --source %s" % (src, src),
        }
        added.append(src)
    if not added:
        print("[来源注册] 全部已注册，无需改动")
        return changes
    print("[来源注册] 新增来源名: %s" % ", ".join(added))
    t = Target("hub-agents", "记忆中枢来源表", "hub", agents_json, True, True, "")
    if dry_run:
        return [Change(t, "replace", "新增来源 %s（预演）" % ", ".join(added))]
    bak = agents_json + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(agents_json, bak)
    with open(agents_json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return [Change(t, "replace", "新增来源 %s（备份 %s）" % (", ".join(added), os.path.basename(bak)))]


# ==========================================================================
# 校验
# ==========================================================================
def do_verify(adapters, spec):
    print("=" * 78)
    print("校验：%d 个客户端" % len(adapters))
    print("=" * 78)
    rows = []
    for a in adapters:
        for scope, path in a.candidates():
            try:
                installed, _r = a.probe(scope, path)
            except Exception:
                installed = False
            if not installed or not os.path.exists(path):
                continue
            try:
                ok, detail = a.read_back(Target(a.id, a.display, scope, path, True, True, ""), spec)
            except Exception as e:
                ok, detail = False, "校验异常: %s" % e
            tstate = ""
            try:
                ts = a.trust_state(spec)
                if ts is not None:
                    tstate = "信任=%s" % ("√" if ts[0] else "×")
            except Exception:
                tstate = "信任=?"
            rows.append((a.id, scope, ok, detail, tstate))
    for cid, scope, ok, detail, tstate in rows:
        print("  [%s] %-22s %-18s %s %s" % ("PASS" if ok else "FAIL", cid, scope, tstate, detail))
    bad = [r for r in rows if not r[2]]
    print()
    print("---- %d/%d ----" % (len(rows) - len(bad), len(rows)))
    if bad:
        print("未通过：%s" % ", ".join(r[0] for r in bad))
    print()
    print("注：这是**静态校验**（配置文件内容 + 信任状态）。")
    print("    「客户端真的连上了」需要客户端重启后再看它的 MCP 状态 —— 见 README「三层判据」。")
    return 1 if bad else 0


# ==========================================================================
# 回滚
# ==========================================================================
def do_rollback(stamp=None):
    if not os.path.isdir(BACKUP_ROOT):
        print("没有备份目录 %s" % BACKUP_ROOT)
        return 1
    stamps = sorted(d for d in os.listdir(BACKUP_ROOT)
                    if os.path.isdir(os.path.join(BACKUP_ROOT, d)))
    if not stamps:
        print("备份目录为空")
        return 1
    stamp = stamp or stamps[-1]
    d = os.path.join(BACKUP_ROOT, stamp)
    if not os.path.isdir(d):
        print("找不到备份批次 %s（可用：%s）" % (stamp, ", ".join(stamps)))
        return 1
    mp = os.path.join(d, "manifest.json")
    print("=" * 78)
    print("回滚批次 %s" % stamp)
    print("=" * 78)
    if not os.path.isfile(mp):
        print("该批次没有 manifest.json，只列出文件供人工处理：")
        for root, _dirs, files in os.walk(d):
            for fn in files:
                print("  %s" % os.path.join(root, fn))
        return 1
    with open(mp, "r", encoding="utf-8") as f:
        man = json.load(f)
    n = 0
    for c in man.get("changes", []):
        b = c.get("backup")
        if not b or not os.path.isfile(b):
            print("  - 跳过（无备份，原本就不存在）: %s" % c.get("path"))
            continue
        try:
            shutil.copy2(b, c["path"])
            print("  √ 已还原 %s" % c["path"])
            n += 1
        except Exception as e:
            print("  × 还原失败 %s: %s" % (c.get("path"), e))
    print()
    print("还原 %d 个文件。★被还原的是「本次接入前」的内容。" % n)
    return 0


# ==========================================================================
# 自检
# ==========================================================================
def do_selftest():
    """不依赖网络、不写任何真实配置的自检。"""
    import tempfile
    ok = []

    def chk(n, c):
        ok.append((n, bool(c)))

    # 1) 适配器齐全
    ads = all_adapters()
    chk("适配器数量 >= 20", len(ads) >= 20)
    chk("每个适配器有 id/display", all(a.id and a.display for a in ads))
    chk("id 唯一", len({a.id for a in ads}) == len(ads))
    chk("都有 candidates()", all(isinstance(a.candidates(), list) for a in ads))

    # 2) 沙箱里跑一遍「新建 + 幂等 + 回滚」
    with tempfile.TemporaryDirectory() as td:
        a = find_adapter("workbuddy-cn")
        spec = ServerSpec(name="memory-hub", command="C:\\py.exe", args=["mcp_server.py"], env={})
        p = os.path.join(td, "mcp.json")
        t = Target(a.id, a.display, "user", p, False, True, "")
        ch = a.write(t, spec, dry_run=False)
        chk("新建配置文件", ch.action == "create" and os.path.exists(p))
        t = Target(a.id, a.display, "user", p, True, True, "")   # ★文件已存在了
        ch2 = a.write(t, spec, dry_run=False)
        chk("幂等（第二次 noop）", ch2.action == "noop")
        ok_r, detail = a.read_back(t, spec)
        chk("回读一致", ok_r)
        # 再写一个不同 server，原有条目必须保住
        spec2 = ServerSpec(name="other", command="C:\\py2.exe", args=[], env={})
        ch3 = a.write(t, spec2, dry_run=False)
        data = jsonc.loads(open(p, encoding="utf-8").read())
        chk("新增第二条且保住第一条",
            ch3.action == "insert" and "memory-hub" in data["mcpServers"] and "other" in data["mcpServers"])

    # 3) 根路径缺失必须失败关闭（不猜着写）
    with tempfile.TemporaryDirectory() as td:
        a = find_adapter("zcode")
        p = os.path.join(td, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{\n  "other": 1\n}\n')
        t = Target(a.id, a.display, "user", p, True, True, "")
        ch = a.write(t, ServerSpec(name="memory-hub", command="c", args=[], env={}), dry_run=False)
        chk("根路径缺失时失败关闭", ch.action == "error")

    # 4) 坏 JSON 不得被覆盖
    with tempfile.TemporaryDirectory() as td:
        a = find_adapter("workbuddy-cn")
        p = os.path.join(td, "mcp.json")
        broken = '{"mcpServers": {"a": }}}'
        with open(p, "w", encoding="utf-8") as f:
            f.write(broken)
        t = Target(a.id, a.display, "user", p, True, True, "")
        ch = a.write(t, ServerSpec(name="x", command="c", args=[], env={}), dry_run=False)
        chk("坏 JSON 报错且原文未变", ch.action == "error" and open(p, encoding="utf-8").read() == broken)

    # 5) 语义等价不该触发写盘（否则会反复改写本来正确的配置）
    with tempfile.TemporaryDirectory() as td:
        a = find_adapter("workbuddy-cn")
        p = os.path.join(td, "mcp.json")
        # 手写一份「分隔符风格不同 + 省了空 env」但语义等价的配置
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write('{\n  "mcpServers": {\n    "x": {\n      "command": "C:/a/b/py.exe",\n'
                    '      "args": ["C:/a/b/s.py"]\n    }\n  }\n}\n')
        before = open(p, encoding="utf-8").read()
        t = Target(a.id, a.display, "user", p, True, True, "")
        ch = a.write(t, ServerSpec(name="x", command=r"C:\a\b\py.exe", args=[r"C:\a\b\s.py"]),
                     dry_run=False)
        chk("语义等价 → noop 不写盘", ch.action == "noop" and open(p, encoding="utf-8").read() == before)
        ok_r, _d = a.read_back(t, ServerSpec(name="x", command=r"C:\a\b\py.exe", args=[r"C:\a\b\s.py"]))
        chk("分隔符风格不同也判为一致", ok_r)

    # 6) 没装的客户端不许造配置（安装判据）
    with tempfile.TemporaryDirectory() as td:
        for cid, marker_ok in (("claude-code", False),):
            a = find_adapter(cid)
            p = os.path.join(td, "x.json")
            t = Target(a.id, a.display, "user", p, False, False, "")
            ins, reason = a.probe("user", p)
            chk("%s 有安装判据（不靠父目录）" % cid,
                bool(a.install_markers) and not ins and "未发现安装痕迹" in reason)

    # 7) Codex TOML：识别原生写法 + 防重复表
    with tempfile.TemporaryDirectory() as td:
        a = find_adapter("codex")
        spec7 = ServerSpec(name="memory-hub", command=r"E:\hub\py.exe", args=[r"E:\hub\mcp.py"])
        p = os.path.join(td, "config.toml")
        # (a) 已用原生 TOML 写法接好 → 应判 noop 且不写盘
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write('[mcp_servers.memory-hub]\ncommand = "E:\\\\hub\\\\py.exe"\n'
                    'args = ["E:\\\\hub\\\\mcp.py"]\n')
        before = open(p, encoding="utf-8").read()
        t = Target("codex", a.display, "user", p, True, True, "")
        ch = a.write(t, spec7, dry_run=False)
        chk("Codex 识别原生 TOML 写法 → noop",
            ch.action == "noop" and open(p, encoding="utf-8").read() == before)
        ok7, _d7 = a.read_back(t, spec7)
        chk("Codex 原生写法回读一致", ok7)
        # (b) 同名表但内容不符 → 必须失败关闭，不许造重复表
        p2 = os.path.join(td, "dup.toml")
        with open(p2, "w", encoding="utf-8", newline="\n") as f:
            f.write('[mcp_servers.memory-hub]\ncommand = "other.exe"\nargs = []\n')
        t2 = Target("codex", a.display, "user", p2, True, True, "")
        ch2 = a.write(t2, spec7, dry_run=False)
        chk("Codex 同名表内容不符 → 失败关闭且不写",
            ch2.action == "error" and open(p2, encoding="utf-8").read().count("[mcp_servers") == 1)

    bad = [n for n, v in ok if not v]
    for n, v in ok:
        print("  [%s] %s" % ("PASS" if v else "FAIL", n))
    print("---- %d/%d ----" % (len(ok) - len(bad), len(ok)))
    return 1 if bad else 0


# ==========================================================================
# CLI
# ==========================================================================
def pick_adapters(args):
    ads = all_adapters()
    if getattr(args, "clients", None):
        want = [x.strip() for x in args.clients.split(",") if x.strip()]
        picked = []
        for w in want:
            a = find_adapter(w)
            if a is None:
                sys.exit("[x] 不认识的客户端 id: %s（用 list 看全部）" % w)
            picked.append(a)
        return picked
    if getattr(args, "local_only", False):
        from clients import local
        return list(local.LOCAL)
    return ads


def build_parser():
    p = argparse.ArgumentParser(
        prog="tether_connect",
        description="把记忆中枢自动接入本机所有 AI 客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("设计原则")[0])
    sub = p.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--clients", help="逗号分隔的客户端 id（默认：全部适配器）")
        sp.add_argument("--local-only", action="store_true", help="只处理本机专属客户端")
        sp.add_argument("--name", default="memory-hub", help="MCP server 名（默认 memory-hub）")
        sp.add_argument("--python", help="跑 MCP server 的解释器（默认：当前解释器）")
        sp.add_argument("--mcp-server", help="mcp_server.py 路径（默认：本仓库里的那份）")
        sp.add_argument("--arg", action="append", help="给 MCP server 的额外参数（可重复）")
        sp.add_argument("--env", action="append", help="环境变量 K=V（可重复）")
        sp.add_argument("--hub-dir", help="记忆中枢目录（给了就顺带注册来源名 agents.json）")

    sp = sub.add_parser("list", help="列出所有支持的客户端")
    sp.add_argument("--json", action="store_true", help="以 JSON 输出")

    sp = sub.add_parser("detect", help="探测本机装了哪些客户端、当前接入状态")
    common(sp)

    sp = sub.add_parser("plan", help="预演（绝不写盘）")
    common(sp)

    sp = sub.add_parser("apply", help="执行接入（自动备份 + 幂等）")
    common(sp)
    sp.add_argument("--dry-run", action="store_true", help="等同 plan")
    sp.add_argument("--yes", action="store_true", help="跳过确认（脚本化时用）")
    sp.add_argument("--no-trust", action="store_true", help="不代写 MCP 信任记录")

    sp = sub.add_parser("verify", help="校验（回读 + 信任状态）")
    common(sp)

    sp = sub.add_parser("rollback", help="从备份还原")
    sp.add_argument("--stamp", help="备份批次（默认最近一次）")

    sub.add_parser("selftest", help="不依赖网络的离线自检")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.cmd or "detect"

    if cmd == "list":
        ads = all_adapters()
        if getattr(args, "json", False):
            print(json.dumps([{"id": a.id, "display": a.display, "source": a.source,
                               "needs_restart": a.needs_restart, "docs": a.docs,
                               "gotchas": a.gotchas} for a in ads],
                             ensure_ascii=False, indent=2))
            return 0
        print("支持的客户端（%d）" % len(ads))
        print("-" * 78)
        for a in ads:
            print("  %-24s %-26s source=%-14s" % (a.id, a.display, a.source or "-"))
            for scope, path in a.candidates():
                print("      %-12s %s" % (scope, path))
        return 0

    if cmd == "selftest":
        return do_selftest()

    if cmd == "rollback":
        return do_rollback(getattr(args, "stamp", None))

    spec = default_spec(args)
    ads = pick_adapters(args)

    if cmd == "detect":
        detect(ads, spec)
        print()
        print("下一步：python tether_connect.py plan%s" %
              (" --clients %s" % args.clients if getattr(args, "clients", None) else ""))
        return 0

    if cmd == "plan":
        do_apply(ads, spec, dry_run=True, yes=True, hub_dir=getattr(args, "hub_dir", None))
        return 0

    if cmd == "apply":
        dry = getattr(args, "dry_run", False)
        do_apply(ads, spec, dry_run=dry, yes=getattr(args, "yes", False),
                 do_trust=not getattr(args, "no_trust", False),
                 hub_dir=getattr(args, "hub_dir", None))
        return 0

    if cmd == "verify":
        return do_verify(ads, spec)

    print("未知子命令: %s" % cmd)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
