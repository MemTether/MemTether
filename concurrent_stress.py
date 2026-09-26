# -*- coding: utf-8 -*-
"""
concurrent_stress.py — N8 v2 (2026-09-26): 真·并发多客户端压测

v1 的问题：三个 writer 用 for 循环顺序跑，只是「多进程」不是「并发」，
无法暴露 hubguard 并发锁的真实竞争窗口。

v2 用 multiprocessing.Barrier 让多个独立进程真正同时开跑，覆盖四个场景：
  S1  3 客户端同时 remember         → 数据完整 + source 归属正确
  S2  remember 与 rebuild 竞争       → 写入不丢、投影不撕裂
  S3  remember 与 search 竞争        → 读写互不阻塞、检索不崩
  S4  两个客户端同时 supersede 同一条 → 锁内串行化，最终一致性
最后跑 PRAGMA integrity_check 兜底。

用法：python concurrent_stress.py            # 全部场景
      python concurrent_stress.py S1 S3     # 只跑指定场景
"""
import io, json, os, sqlite3, subprocess, sys, tempfile, time, shutil
import multiprocessing as mp

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HUB = r"E:\RUANJIAN\memory_hub"
GATEWAY = os.path.join(HUB, "gateway.py")
VENV_PY = os.path.join(HUB, ".venv-memory", "Scripts", "python.exe")
PY = os.environ.get("MEM_PY") or (VENV_PY if os.path.exists(VENV_PY) else sys.executable)

SCENARIOS = ("S1", "S2", "S3", "S4")


def make_env(db_path):
    env = os.environ.copy()
    env["MEM_DB"] = db_path
    env["MEM_PROJ_PATH"] = os.path.join(os.path.dirname(db_path), "MEMORY.md")
    env["MEM_EMBED_BACKEND"] = "zhipu"  # local ONNX load 2.3s+并发争抢→spikes 29s; zhipu ~0.2s
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def init_db(db_path):
    """独立临时库：不动生产 memory.db。schema 与 memory_hub 对齐（最小集）。"""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT UNIQUE,
        type TEXT DEFAULT 'fact',
        subject TEXT DEFAULT 'user',
        content TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        superseded_by TEXT,
        valid_from TEXT,
        valid_to TEXT,
        source TEXT DEFAULT 'unknown',
        scope TEXT DEFAULT 'shared',
        confidence REAL DEFAULT 0.8,
        tags TEXT,
        created_at TEXT,
        updated_at TEXT,
        recorded_at TEXT,
        invalidated_at TEXT,
        temporal_source TEXT,
        q_value REAL DEFAULT 0.5,
        use_count INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS supersessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        old_uid TEXT, new_uid TEXT, reason TEXT, by_agent TEXT, ts TEXT
    );
    CREATE TABLE IF NOT EXISTS tool_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT UNIQUE, name TEXT, path TEXT, entrypoint TEXT,
        description TEXT, prerequisites TEXT, status TEXT DEFAULT 'active',
        aliases TEXT, type TEXT, capabilities TEXT
    );
    CREATE TABLE IF NOT EXISTS tool_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_uid TEXT, event TEXT, ts TEXT
    );
    """)
    conn.commit()
    conn.close()


def run_cli(args, db_path, timeout=90):
    env = make_env(db_path)
    return subprocess.run([PY, GATEWAY] + args, env=env, cwd=HUB,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


# ---------- 子进程目标（顶层函数，Windows spawn 可 pickle） ----------


def writer_proc(barrier, db_path, source, n, out_q):
    try:
        barrier.wait(timeout=60)
        ok, errs = True, []
        for i in range(n):
            content = "%s 并发写入第%d条（N8-v2） uid_%d_%s" % (source, i, i, str(time.time_ns())[-6:])
            r = run_cli(["remember", content, "--type", "fact", "--source", source], db_path, timeout=180)
            if r.returncode != 0:
                ok = False
                errs.append((r.stderr or r.stdout or "")[-200:])

        out_q.put({"proc": source, "ok": ok, "errs": errs})
    except Exception as e:
        out_q.put({"proc": source, "ok": False, "errs": [repr(e)]})


def rebuild_proc(barrier, db_path, out_q, rounds=2):
    try:
        barrier.wait(timeout=60)
        errs, ok = [], True
        for _ in range(rounds):
            r = run_cli(["rebuild"], db_path, timeout=120)
            if r.returncode != 0 or '"ok": true' not in (r.stdout or ""):
                ok = False
                errs.append((r.stderr or r.stdout or "")[-300:])
        out_q.put({"proc": "rebuild", "ok": ok, "errs": errs})
    except Exception as e:
        out_q.put({"proc": "rebuild", "ok": False, "errs": [repr(e)]})


def search_proc(barrier, db_path, out_q, rounds=6):
    try:
        barrier.wait(timeout=60)
        errs, ok = [], True
        for i in range(rounds):
            r = run_cli(["search", "并发写入第%d条" % (i % 5)], db_path)
            if r.returncode != 0:
                ok = False
                errs.append((r.stderr or r.stdout or "")[-200:])
        out_q.put({"proc": "search", "ok": ok, "errs": errs})
    except Exception as e:
        out_q.put({"proc": "search", "ok": False, "errs": [repr(e)]})


def supersede_proc(barrier, db_path, old_uid, tag, out_q):
    try:
        barrier.wait(timeout=60)
        r = run_cli(["correct", old_uid, "%s supersede race N8-v2 uid_%s" % (tag, str(time.time_ns())[-6:]),
                     "--reason", "concurrent race S4", "--source", tag], db_path)
        out_q.put({"proc": tag, "rc": r.returncode,
                   "out": (r.stdout or "")[-200:], "err": (r.stderr or "")[-200:]})
    except Exception as e:
        out_q.put({"proc": tag, "rc": -1, "out": "", "err": repr(e)})


# ---------- 场景 ----------

def scenario(db_path, name):
    ctx = mp.get_context("spawn")
    out_q = ctx.Queue()
    post = None

    if name == "S1":
        sources = ["codex", "workbuddy", "doubao_a"]
        barrier = ctx.Barrier(len(sources))
        procs = [ctx.Process(target=writer_proc, args=(barrier, db_path, s, 5, out_q))
                 for s in sources]
        expect_by_src = {s: 5 for s in sources}
        expect_total = 15
    elif name == "S2":
        sources = ["codex", "workbuddy"]
        barrier = ctx.Barrier(len(sources) + 1)
        procs = [ctx.Process(target=writer_proc, args=(barrier, db_path, s, 5, out_q))
                 for s in sources]
        procs.append(ctx.Process(target=rebuild_proc, args=(barrier, db_path, out_q, 2)))
        expect_by_src = {s: 5 for s in sources}
        expect_total = 10
    elif name == "S3":
        sources = ["codex", "doubao_a"]
        barrier = ctx.Barrier(len(sources) + 1)
        procs = [ctx.Process(target=writer_proc, args=(barrier, db_path, s, 5, out_q))
                 for s in sources]
        procs.append(ctx.Process(target=search_proc, args=(barrier, db_path, out_q, 6)))
        expect_by_src = {s: 5 for s in sources}
        expect_total = 10
    else:  # S4
        seed = run_cli(["remember", "S4 种子条目：将被两条 correct 竞争",
                        "--type", "fact", "--source", "codex"], db_path)
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT uid FROM facts WHERE content LIKE 'S4 种子条目%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return {"scenario": name, "ok": False,
                    "err": "seed not created", "stdout": (seed.stdout or "")[-300:]}
        old_uid = row[0]
        barrier = ctx.Barrier(2)
        procs = [ctx.Process(target=supersede_proc, args=(barrier, db_path, old_uid, tag, out_q))
                 for tag in ("codex", "workbuddy")]
        expect_by_src = {}
        expect_total = None
        post = old_uid

    t0 = time.time()
    for p in procs:
        p.start()
    results = [out_q.get(timeout=300) for _ in procs]
    for p in procs:
        p.join(timeout=30)
        if p.is_alive():
            p.terminate()
    wall = round(time.time() - t0, 2)

    conn = sqlite3.connect(db_path)
    if post:
        orig = conn.execute("SELECT status, superseded_by FROM facts WHERE uid=?",
                            (post,)).fetchone()
        new_rows = conn.execute(
            "SELECT uid, status, superseded_by FROM facts "
            "WHERE content LIKE '%N8-v2%' AND content LIKE '%supersede race%'").fetchall()
        supersessions = conn.execute("SELECT COUNT(*) FROM supersessions").fetchone()[0]
        rc_ok = sum(1 for r in results if r.get("rc") == 0)
        chain_ok = orig is not None and orig[0] == "superseded"
        ok = rc_ok >= 1 and chain_ok
        detail = {"orig_status": orig[0] if orig else None,
                  "winners": len(new_rows), "rc_ok": rc_ok,
                  "supersessions_total": supersessions, "procs": results}
    else:
        total = conn.execute(
            "SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]
        by_src = dict(conn.execute(
            "SELECT source, COUNT(*) FROM facts WHERE status='active' "
            "GROUP BY source").fetchall())
        data_ok = total == expect_total
        src_ok = all(by_src.get(s, 0) == n for s, n in expect_by_src.items())
        proc_ok = all(r.get("ok") for r in results)
        ok = data_ok and src_ok and proc_ok
        detail = {"total": total, "expected": expect_total,
                  "by_source": by_src, "procs": results}
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    ok = ok and integrity == "ok"
    return {"scenario": name, "wall_s": wall, "ok": ok,
            "integrity": integrity, "detail": detail}


_T0 = time.time()


def main():
    only = [a.upper() for a in sys.argv[1:] if a.upper() in SCENARIOS] or list(SCENARIOS)
    out = {"test": "hubguard concurrent stress v2 (true parallel)", "scenarios": []}
    for name in only:
        sdir = tempfile.mkdtemp(prefix="hg-stress-v2-%s-" % name)
        db = os.path.join(sdir, "memory.db")
        init_db(db)
        r = scenario(db, name)
        out["scenarios"].append(r)
        print(json.dumps(r, ensure_ascii=False))
        shutil.rmtree(sdir, ignore_errors=True)
    out["verdict"] = "PASS" if all(s.get("ok") for s in out["scenarios"]) else "FAIL"
    out["wall_s"] = round(time.time() - _T0, 2)
    print(json.dumps({"verdict": out["verdict"], "wall_s": out["wall_s"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    mp.freeze_support()
    main()

