# -*- coding: utf-8 -*-
"""memtether.py —— 包门面 + 命令行入口。

为什么需要这个文件
------------------
pip 安装后用户期望两件事都能用：`import memtether` 和 `memtether <子命令>`。
但本项目的引擎模块（gateway.py / mem.py / memsearch.py …）是**平铺**在仓库根的，
彼此用 `import gateway` 这类顶层导入互相引用 —— 这是历史结构，且生产环境
（memory_hub 按脚本路径直接调用）依赖它，**不能改**。

本文件做两件事，且都只做这一处：

  1. **门面**：把常用能力 re-export 成 `memtether.search()` / `memtether.remember()`；
     并在此处一次性把包目录加入 `sys.path`，让平铺导入在安装后依然成立
     （否则 wheel 里 `import gateway` 会 ModuleNotFoundError）。
  2. **CLI**：`[project.scripts] memtether = "memtether:main"` 的入口。

★这里是把「引擎平铺结构」与「标准 pip 包」缝合起来的**唯一接缝**。
  换目录结构（如 src-layout 真包）要动的就是这一处 + pyproject 的 py-modules。

数据目录
--------
默认 `~/.memtether/`（可用 `MEMTETHER_HOME` 覆盖），库文件 `memory.db`。
零配置开跑：`memtether demo` 生成一份**全合成**演示库即可（无任何真实数据）。
"""
import argparse
import json
import os
import subprocess
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ---------------------------------------------------------------- 路径缝合
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    # ★必须在导入任何引擎模块之前执行。引擎之间是平铺导入（`import gateway` /
    #   `import memsearch`），只有包目录在 sys.path 上才解析得到。
    sys.path.insert(0, _HERE)

__version__ = "0.1.0a19"

DATA_DIR = os.environ.get("MEMTETHER_HOME") or os.path.join(
    os.path.expanduser("~"), ".memtether")
DEFAULT_DB = os.path.join(DATA_DIR, "memory.db")

# ★引擎的库路径是**模块级常量**（import 时求值，见 gateway.py:50 / memsearch.py:28），
#   所以必须在导入前把环境变量摆好。用 setdefault：用户显式给了 MEM_DB 就不覆盖。
os.environ.setdefault("MEM_DB", DEFAULT_DB)

__all__ = ["search", "remember", "stats", "demo", "main", "__version__"]


# ---------------------------------------------------------------- 延迟导入
def _engine(name):
    """按需导入引擎模块。

    ★不做顶层 import：引擎在导入时会读 MEM_DB / MEM_STORE 并可能触碰文件系统。
      延迟到真正调用时再导入，`memtether --help` / `--version` 才能在
      「还没生成库」的环境下正常返回。
    """
    import importlib
    return importlib.import_module(name)


# ---------------------------------------------------------------- 公共 API
def search(query, limit=10, **kw):
    """混合检索（关键词 + 向量 + 字面，RRF 融合 + 精排）。

    返回 `{'query': ..., 'results': [ {uid, content, type, source, score, …}, … ]}`
    （与 memsearch.search_hybrid 同构，不在这里另造一套数据结构）。

    演示库场景下没有向量库 → 语义路优雅降级，关键词/字面路照常工作。
    """
    return _engine("memsearch").search_hybrid(query, limit=limit, **kw)


def remember(content, type="fact", source="memtether", **kw):
    """写入一条记忆。

    `source` 不是可选项：多个 agent 共享同一份物理库，没有归属就是灾难
    （项目铁律，见 gateway.py 文件头第 2 条）。
    """
    return _engine("gateway").remember(content, type=type, source=source, **kw)


def stats():
    """库内统计（条数 / 类型分布 / 来源分布）。"""
    return _engine("gateway").stats()


def demo(out=None, force=False, seed=None):
    """生成全合成演示库，返回输出路径。

    ★实现要点（2026-09-16 修，三个坑都在这里）：

      ① **必须走子进程，不能 `import` 后直接调 `main()`** ——
         `scripts/make_demo_db.py` 的 `main()` 不接 argv（内部 `parse_args()` 读
         `sys.argv`），且**无条件 `sys.exit()`**（自检退出码就是它的返回值）。
         直接调用会 TypeError，且退出码被吞掉 —— 那是「跑起来不报错、结果全错」。
      ② **不能把脚本复制/同步到别处** —— 它靠 `ROOT/gateway.py` 正则抽 `SCHEMA`
         （"唯一真源，禁止手抄"，抽不到**硬报错**），`ROOT` = 它自己的上一级目录。
         只有留在 `scripts/` 里，`ROOT` 才恰好是引擎所在处（安装后同样是
         site-packages）。所以 `scripts/` 必须随 wheel 分发。
      ③ **必须带 `MEM_DB`** —— 脚本内部的功能冒烟（`smoke()`）用子进程 + `MEM_DB`
         真跑一遍 `gateway.stats` / `memsearch.asset_text` / `gateway.search`，
         验的就是"用户 clone 后跑的那条路"。不给它 `MEM_DB`，冒烟就验错了库。

    所以这里与 `make_demo_db.smoke()` 用同款调用方式：子进程 + `MEM_DB` +
    UTF-8 环境，退出码原样上抛；脚本自带自检（泄密零命中 / schema 一致 /
    功能冒烟），非 0 即失败。

    ★★2026-09-16 实测坑（务必保留 PIPE + 回显）：Windows 上
      `subprocess.run(..., creationflags=CREATE_NO_WINDOW)` 且 **stdout/stderr
      为 None（继承父句柄）** 时，子进程的输出会被**静默丢弃** ——
      子进程正常跑完、退出码 0，只是它打印的东西一个字都看不见。
      CPython 在 stdout/stderr 为 None 时不会设 STARTF_USESTDHANDLES，
      而 CREATE_NO_WINDOW 又给子进程建了个不可见的控制台 → 输出写进了那个黑洞。
      后果：`memtether demo` 只报「✓ 就绪」，**三项自检报告全部消失**
      —— 正是本项目要治的「跑起来不报错、但结果全错」。
      修法：**捕获再回显**（PIPE + 转发到自己的 stdout）。这样既保住
      CREATE_NO_WINDOW 的"不弹黑框"，又保证报告一定看得见。
    """
    out = os.path.abspath(out or DEFAULT_DB)
    if os.path.exists(out) and not force:
        raise FileExistsError(
            "%s 已存在；要覆盖请显式传 force=True（或 CLI 加 --force）" % out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    gen = os.path.join(_HERE, "scripts", "make_demo_db.py")
    if not os.path.exists(gen):
        raise RuntimeError(
            "演示库生成器缺失：%s\n"
            "  → wheel 未包含 scripts/ 包（检查 pyproject 的 packages 配置）" % gen)

    cmd = [sys.executable, gen, "--out", out]
    if seed is not None:
        cmd += ["--seed", str(seed)]

    env = dict(os.environ)
    env["MEM_DB"] = out
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    proc = subprocess.run(
        cmd, cwd=_HERE, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    report = (proc.stdout or b"").decode("utf-8", "replace").rstrip()
    if report:
        # 原样回显：自检报告就是给用户看的（也是失败时的唯一线索）
        sys.stdout.write(report + "\n")
        sys.stdout.flush()
    if proc.returncode != 0:
        raise RuntimeError(
            "演示库生成 / 自检失败，退出码 %s（详见上方报告）" % proc.returncode)
    return out


# ---------------------------------------------------------------- CLI
def _cmd_demo(args):
    if os.path.exists(args.out) and not args.force:
        print("★%s 已存在；要覆盖请加 --force" % args.out)
        return 1
    try:
        path = demo(args.out, force=args.force)
    except Exception as exc:  # noqa: BLE001 —— CLI 边界，转成人话
        print("★生成演示库失败：%s" % exc)
        return 1
    print("\n✓ 演示库就绪：%s" % path)
    print("  接着试：memtether search \"演示查询\"")
    return 0


def _cmd_search(args):
    res = search(args.query, limit=args.limit)
    rows = res.get("results", []) if isinstance(res, dict) else (res or [])
    if not rows:
        print("（无结果）")
        return 0
    listing = getattr(args, "list", False)
    for i, r in enumerate(rows, 1):
        if isinstance(r, dict):
            text = r.get("content") or r.get("text") or str(r)
            kind = r.get("type") or ""
            src_attr = r.get("source") or ""
            score = r.get("score")
            uid = r.get("uid") or r.get("id") or ""
            head = "[%s%s]" % (kind, ("/" + src_attr) if src_attr else "")
            tail = ("  %.4f" % score) if isinstance(score, (int, float)) else ""
            if listing:
                # L0 mode: first line only + uid + score; agent decides to read full
                first_line = text.split("\n")[0][:120]
                uid_str = ("  uid=" + uid) if uid else ""
                print("%2d. %s %s%s%s" % (i, head, first_line, uid_str, tail))
            else:
                print("%2d. %s %s%s" % (i, head, text, tail))
        else:
            print("%2d. %s" % (i, r))
    return 0


def _cmd_remember(args):
    uid = remember(args.content, type=args.type, source=args.source)
    print("✓ 已写入：%s" % (uid or "(ok)"))
    return 0


def _cmd_stats(_args):
    s = stats()
    if isinstance(s, dict):
        for k, v in s.items():
            print("%-16s %s" % (k, v))
    else:
        print(s)
    return 0



def _cmd_init(args):
    """Initialize a new memory DB."""
    db_path = os.environ.get("MEM_DB") or DEFAULT_DB
    if os.path.exists(db_path) and not getattr(args, "force", False):
        print(f"DB already exists: {db_path}")
        print("Use --force to overwrite")
        return
    
    # Call the existing demo generator
    here = os.path.dirname(os.path.abspath(__file__))
    demo_script = os.path.join(here, "scripts", "make_demo_db.py")
    if os.path.exists(demo_script):
        import subprocess
        r = subprocess.run([sys.executable, demo_script, "--out", db_path],
                          capture_output=True, text=True)
        if r.returncode == 0:
            print(f"✅ Created memory DB: {db_path}")
        else:
            print(f"❌ Failed to create DB: {r.stderr[:200]}")
    else:
        # Fallback: create empty DB
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.close()
        print(f"✅ Created empty memory DB: {db_path}")


def _cmd_connect(args):
    """Connect AI clients to the memory hub."""
    # Delegate to tether_connect.py
    here = os.path.dirname(os.path.abspath(__file__))
    connect_script = os.path.join(here, "tether_connect.py")
    if not os.path.exists(connect_script):
        print("❌ tether_connect.py not found")
        return
    
    import subprocess
    cmd_args = [sys.executable, connect_script]
    if getattr(args, "all", False):
        cmd_args.append("apply")
    elif getattr(args, "client", None):
        cmd_args.extend(["apply", "--client", args.client])
    else:
        cmd_args.append("detect")
    
    os.execv(sys.executable, cmd_args)



def _cmd_init(args):
    """Initialize a new memory DB with demo data."""
    db_path = os.environ.get("MEM_DB") or DEFAULT_DB
    if os.path.exists(db_path) and not getattr(args, "force", False):
        print(f"DB already exists: {db_path}")
        print("Use --force to overwrite")
        return
    here = os.path.dirname(os.path.abspath(__file__))
    demo_script = os.path.join(here, "scripts", "make_demo_db.py")
    if os.path.exists(demo_script):
        import subprocess
        r = subprocess.run([sys.executable, demo_script, "--out", db_path],
                          capture_output=True, text=True)
        if r.returncode == 0:
            print(f"Created memory DB: {db_path}")
        else:
            print(f"Failed: {r.stderr[:200]}")
    else:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        import sqlite3
        sqlite3.connect(db_path).close()
        print(f"Created empty memory DB: {db_path}")


def _cmd_connect(args):
    """Connect AI clients to the memory hub (delegates to tether_connect)."""
    here = os.path.dirname(os.path.abspath(__file__))
    connect_script = os.path.join(here, "tether_connect.py")
    if not os.path.exists(connect_script):
        print("tether_connect.py not found")
        return
    import subprocess
    cmd = [sys.executable, connect_script]
    if getattr(args, "all", False):
        cmd.append("apply")
    else:
        cmd.append("detect")
    os.execv(sys.executable, cmd)




def _cmd_export_md(args):
    """Export active memories as markdown files — portable, greppable, git-able."""
    import sqlite3, datetime
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    db_path = os.environ.get("MEM_DB") or DEFAULT_DB
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT uid, type, content, source, created_at, updated_at FROM facts WHERE status='active' ORDER BY type, created_at"
    ).fetchall()
    manifest = []
    for r in rows:
        uid = r['uid'] or 'unknown'
        typ = r['type'] or 'fact'
        ts = r['created_at'] or ''
        fn = f"{typ}_{uid[:30]}.md"
        fp = os.path.join(out_dir, fn)
        content = r['content'] or ''
        frontmatter = f"""---
uid: {uid}
type: {typ}
source: {r['source'] or 'unknown'}
created: {ts}
updated: {r['updated_at'] or ''}
---

"""
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(frontmatter + content + chr(10))
        manifest.append({'file': fn, 'uid': uid, 'type': typ, 'source': r['source']})
    conn.close()
    # write manifest.json
    mf = os.path.join(out_dir, 'manifest.json')
    with open(mf, 'w', encoding='utf-8') as f:
        json.dump({'exported_at': datetime.datetime.now().isoformat(), 'count': len(manifest), 'items': manifest}, f, ensure_ascii=False, indent=1)
    print(f"✓ Exported {len(manifest)} memories → {out_dir}/ ({len(manifest)} .md files + manifest.json)")
    return 0


def _cmd_setup(args):
    """One-command setup: write MCP config for a specific client, then verify."""
    here = os.path.dirname(os.path.abspath(__file__))
    connect_script = os.path.join(here, "tether_connect.py")
    if not os.path.exists(connect_script):
        print("tether_connect.py not found"); return 1
    client = (args.client or "").strip()
    if client.lower() == "list":
        import subprocess
        r = subprocess.run([sys.executable, connect_script, "detect"], capture_output=False)
        return r.returncode if hasattr(r, 'returncode') else 0
    import subprocess
    print(f"[setup] Connecting {client} ...")
    r = subprocess.run([sys.executable, connect_script, "apply", "--client", client])
    rc = r.returncode if hasattr(r, 'returncode') else 0
    if rc == 0:
        print(f"[setup] {client} connected. Restart the client to pick up the new MCP config.")
        print(f"[setup] Verify: memtether search \"test\"  (from inside the client)")
    else:
        print(f"[setup] Failed to connect {client} (rc={rc}). Run 'memtether connect detect' to debug.")
    return rc


def _cmd_dashboard(args):
    """Start the API server and open the web dashboard."""
    import threading, webbrowser, time
    port = args.port
    url = f"http://localhost:{port}/dashboard"
    
    def _open_browser():
        time.sleep(3)
        webbrowser.open(url)
    
    print(f"Starting MemTether API server on port {port}...")
    print(f"Dashboard: {url}")
    print("Press Ctrl+C to stop.")
    
    threading.Thread(target=_open_browser, daemon=True).start()
    
    try:
        import uvicorn
        uvicorn.run("api_server:app", host="0.0.0.0", port=port, log_level="info")
    except KeyboardInterrupt:
        print("\nServer stopped.")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        prog="memtether",
        description="MemTether —— 跨客户端 AI 记忆中枢（一份物理记忆，多个客户端共用）")
    p.add_argument("--version", action="version",
                   version="memtether %s" % __version__)
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("demo", help="生成全合成演示库（零真实数据，可离线跑）")
    sp.add_argument("--out", default=DEFAULT_DB, help="输出路径（默认 %s）" % DEFAULT_DB)
    sp.add_argument("--force", action="store_true", help="已存在时覆盖")
    sp.set_defaults(func=_cmd_demo)

    sp = sub.add_parser("search", help="混合检索")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--list", action="store_true",
                    help="L0 mode: return one-line summaries + UIDs instead of full content")
    sp.set_defaults(func=_cmd_search)

    sp = sub.add_parser("remember", help="写入一条记忆（必须带 source 归属）")
    sp.add_argument("content")
    sp.add_argument("--type", default="fact",
                    help="fact/experience/decision/incident/todo")
    sp.add_argument("--source", default="memtether")
    sp.set_defaults(func=_cmd_remember)

    sp = sub.add_parser("stats", help="统计")
    sp.set_defaults(func=_cmd_stats)

    sp = sub.add_parser("init", help="Initialize a new memory DB (with demo data)")
    sp.add_argument("--force", action="store_true", help="Overwrite existing DB")
    sp.set_defaults(func=_cmd_init)

    sp = sub.add_parser("connect", help="Connect AI clients to the memory hub")
    sp.add_argument("--all", action="store_true", help="Connect all detected clients")
    sp.add_argument("client", nargs="?", default=None, help="Specific client")
    sp.set_defaults(func=_cmd_connect)

    sp = sub.add_parser("export-md", help="Export memories as markdown files (portable, greppable, git-able)")
    sp.add_argument("--out", default="./memtether-export", help="Output directory")
    sp.set_defaults(func=_cmd_export_md)

    sp = sub.add_parser("setup", help="One-command setup for a specific AI client (writes MCP config + verifies)")
    sp.add_argument("client", help="Client id: claude-code, cursor, windsurf, gemini-cli, codex, claude-desktop, ... (run 'memtether setup list' to see all)")
    sp.set_defaults(func=_cmd_setup)

    sp = sub.add_parser("dashboard", help="Start the web dashboard (API server + browser UI)")
    sp.add_argument("--port", type=int, default=8820, help="Port to run on (default 8820)")
    sp.set_defaults(func=_cmd_dashboard)

    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
