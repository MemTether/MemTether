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

# P1-fix (a41): single-source version. pyproject.toml is the build truth;
# importlib.metadata reflects what pip actually installed (they can diverge
# in a repo checkout, which is why __version__ drifted a30 vs a40 for 9 releases).
try:
    # repo checkout: pyproject.toml sits next to this file and is the freshest
    # truth; installed wheel: no pyproject next to it → importlib.metadata.
    _pp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyproject.toml")
    if os.path.exists(_pp):
        import tomllib
        with open(_pp, "rb") as _pf:
            __version__ = tomllib.load(_pf)["project"]["version"]
    else:
        from importlib.metadata import version as _meta_version
        __version__ = _meta_version("memtether")
except Exception:
    __version__ = "0.0.0.dev0"

DATA_DIR = os.environ.get("MEMTETHER_HOME") or os.path.join(
    os.path.expanduser("~"), ".memtether")
try:
    import memtether_paths as _mp
    DEFAULT_DB = _mp.default_db()  # P4: unified default
except Exception:
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



# ---------------------------------------------------------------- P4: import
def _cmd_conflicts(args):
    """Review conflict-review candidates (P0-3, 2026-10-06).

    Governance detection writes pairs with verdict='pending'; this command
    closes the loop: list -> accept (confirm conflict) / discard (no_conflict).
    Discarded pairs are excluded from governance scoring via
    governance.reviewed_no_conflict().
    """
    import sqlite3
    db_path = os.environ.get("MEM_DB") or DEFAULT_DB
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS conflict_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid_a TEXT, uid_b TEXT,
        verdict TEXT, note TEXT, by_agent TEXT, ts TEXT)""")

    if args.stats:
        rows = conn.execute("SELECT verdict, COUNT(*) n FROM conflict_reviews GROUP BY verdict").fetchall()
        if not rows:
            print("  (no conflict reviews recorded — run governance detection first)")
        for r in rows:
            print(f"  {r['verdict']}: {r['n']}")
        conn.close()
        return 0

    if args.list:
        rows = conn.execute(
            "SELECT id, uid_a, uid_b FROM conflict_reviews WHERE verdict='pending' LIMIT ?",
            (args.limit,)).fetchall()
        if not rows:
            print("  (no pending conflict candidates — governance detection writes them)")
        for r in rows:
            ca = conn.execute("SELECT content FROM facts WHERE uid=?", (r['uid_a'],)).fetchone()
            cb = conn.execute("SELECT content FROM facts WHERE uid=?", (r['uid_b'],)).fetchone()
            sa = (ca['content'] if ca else r['uid_a'])[:60]
            sb = (cb['content'] if cb else r['uid_b'])[:60]
            print(f"  #{r['id']}  A: {sa}")
            print(f"        B: {sb}")
        conn.close()
        return 0

    if args.accept or args.discard:
        verdict = 'conflict' if args.accept else 'no_conflict'
        ids = (args.accept or args.discard).split(',')
        by = args.by or 'cli'
        n = 0
        q_bumped = 0
        # a46: accept = the kept (newer) fact's assertion survives -> reward it;
        #      discard = the pair was noise -> mild penalty on both to damp future
        #      false candidates. Q-Value learning loop (P0-3 / bug fix #2).
        import gateway as _gw
        reward = 1.0 if args.accept else 0.0
        for i in ids:
            i = i.strip()
            if not i.isdigit():
                continue
            row = conn.execute(
                "SELECT uid_a, uid_b FROM conflict_reviews WHERE id=? AND verdict='pending'",
                (int(i),)).fetchone()
            if row is None:
                continue
            cur = conn.execute(
                "UPDATE conflict_reviews SET verdict=?, note=?, by_agent=?, ts=datetime('now','localtime') "
                "WHERE id=? AND verdict='pending'", (verdict, args.note or '', by, int(i)))
            n += cur.rowcount
            conn.commit()  # release write lock before bump_qvalue opens its own conn (a46: fixes 'database is locked')
            if cur.rowcount:
                # pick the newer fact as the "kept" side for accept
                try:
                    ua = conn.execute("SELECT updated_at FROM facts WHERE uid=?", (row['uid_a'],)).fetchone()
                    ub = conn.execute("SELECT updated_at FROM facts WHERE uid=?", (row['uid_b'],)).fetchone()
                    keep = row['uid_a'] if (ua and ub and ua['updated_at'] >= ub['updated_at']) else row['uid_b']
                    _gw.bump_qvalue(uid=keep, reward=reward, agent=by,
                                    detail=f'conflict review #{i} {verdict}')
                    if not args.accept:
                        loser = row['uid_b'] if keep == row['uid_a'] else row['uid_a']
                        _gw.bump_qvalue(uid=loser, reward=0.0, agent=by,
                                        detail=f'conflict review #{i} {verdict}')
                    q_bumped += 1
                except Exception as _e:
                    print(f"⚠ q_value bump failed: {_e}", file=sys.stderr)  # best-effort; review itself already recorded
        conn.commit()
        print(f"✓ reviewed {n} pair(s) as {verdict}" + (f" (q_value bumped: {q_bumped})" if q_bumped else ""))
        conn.close()
        return 0

    conn.close()
    print("nothing to do: use --stats / --list / --accept ID[,ID] / --discard ID[,ID]")
    return 1


def _cmd_remember_batch(args):
    """Batch write: read a JSON array of {content, type, source} objects and
    insert them with a single commit + batch ChromaDB upsert.

    5000 facts: 7min (one-by-one) -> <60s (batch)."""
    import json as _json
    import sqlite3
    import time as _t
    db_path = os.environ.get("MEM_DB") or DEFAULT_DB

    input_path = args.file
    if not os.path.isfile(input_path):
        print(f"✗ file not found: {input_path}")
        return 1
    with open(input_path, encoding="utf-8") as f:
        items = _json.load(f)
    if not isinstance(items, list):
        print("✗ input must be a JSON array")
        return 1

    import gateway
    conn = gateway.get_conn()
    conn.row_factory = sqlite3.Row
    gateway.init_db()

    inserted = 0
    deduped = 0
    errors = 0
    uids = []
    vec_batch = []

    t0 = _t.perf_counter()
    for i, item in enumerate(items):
        content = item.get("content", "").strip()
        if not content or len(content) > 10240:
            errors += 1
            continue
        itype = item.get("type", "fact")
        isource = item.get("source", "batch")
        uid = gateway._uid("fact", content + isource)
        # check for existing
        existing = conn.execute(
            "SELECT uid FROM facts WHERE uid=? AND status='active'", (uid,)).fetchone()
        if existing:
            deduped += 1
            continue
        try:
            conn.execute(
                """INSERT INTO facts (uid,type,subject,content,status,source,scope,confidence,tags,
                                      created_at,updated_at,valid_from,recorded_at,temporal_source,tenant_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid, itype, "user", content, "active", isource, "shared", 0.8, "",
                 gateway.now(), gateway.now(), gateway.now(), gateway.now(), "native",
                 os.environ.get("MEM_TENANT_ID", "default")))
            gateway.audit(conn, "remember", uid, isource, content[:60])
            uids.append(uid)
            vec_batch.append(content)
            inserted += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  ⚠ item {i}: {e}")

    conn.commit()

    # batch vector upsert (single embed call for all)
    vec_ok = 0
    if vec_batch:
        try:
            import memsearch
            col = memsearch._client().get_or_create_collection(
                memsearch.COLLECTION, metadata={"hnsw:space": "cosine"})
            vecs = memsearch._embed(vec_batch)
            col.upsert(ids=uids, embeddings=vecs, documents=vec_batch,
                       metadatas=[{"uid": u, "type": "fact"} for u in uids])
            vec_ok = len(uids)
        except Exception as e:
            print(f"  ⚠ vector upsert failed (facts still in SQLite): {e}")

    dt = _t.perf_counter() - t0
    print(f"✓ batch: {inserted} inserted, {deduped} deduped, {errors} errors in {dt:.1f}s "
          f"({inserted/dt:.0f}/sec)" if dt > 0 else "✓ batch complete")
    print(f"  vectors: {vec_ok}/{len(uids)}")
    conn.close()
    return 0


def _cmd_reconcile(args):
    """Reconcile SQLite facts ↔ ChromaDB vector store.

    Detects and fixes:
    - facts in SQLite but missing from ChromaDB (vector gap)
    - vectors in ChromaDB but not in active SQLite facts (ghost vectors)

    This runs incrementally: only the differences are fixed.
    """
    import sys as _sys
    db_path = os.environ.get("MEM_DB") or DEFAULT_DB
    import gateway
    conn = gateway.get_conn()
    gateway.init_db()

    try:
        import memsearch
        col = memsearch._client().get_or_create_collection(
            memsearch.COLLECTION, metadata={"hnsw:space": "cosine"})
    except Exception as e:
        print(f"✗ ChromaDB unavailable: {e}")
        conn.close()
        return 1

    # 1. get SQLite active uid set
    sql_uids = set(r[0] for r in conn.execute(
        "SELECT uid FROM facts WHERE status='active'").fetchall())

    # 2. get ChromaDB stored uid set (batch get all)
    chroma_ids = set()
    try:
        got = col.get(include=[])
        if got and got.get("ids"):
            chroma_ids = set(got["ids"])
    except Exception as e:
        print(f"⚠ could not list ChromaDB entries: {e}")

    # 3. diff
    missing_vec = sql_uids - chroma_ids  # in SQLite but not in vector store
    ghost_vec = chroma_ids - sql_uids     # in vector store but not in active facts

    print(f"SQLite active facts: {len(sql_uids)}")
    print(f"ChromaDB stored vectors: {len(chroma_ids)}")
    print(f"Missing vectors (need upsert): {len(missing_vec)}")
    print(f"Ghost vectors (need delete): {len(ghost_vec)}")

    if not missing_vec and not ghost_vec:
        print("✓ SQLite ↔ ChromaDB consistent. No action needed.")
        conn.close()
        return 0

    # 4. fix missing vectors (batch upsert)
    fixed = 0
    if missing_vec and args.apply and not args.dry_run:
        uid_list = sorted(missing_vec)
        contents = []
        valid_uids = []
        for u in uid_list:
            r = conn.execute("SELECT content FROM facts WHERE uid=? AND status='active'", (u,)).fetchone()
            if r:
                contents.append(r[0])
                valid_uids.append(u)
        if valid_uids:
            try:
                vecs = memsearch._embed(contents)
                col.upsert(ids=valid_uids, embeddings=vecs, documents=contents,
                           metadatas=[{"uid": u, "type": "fact"} for u in valid_uids])
                fixed = len(valid_uids)
                print(f"  ✓ upserted {fixed} missing vectors")
            except Exception as e:
                print(f"  ✗ batch upsert failed: {e}")
    elif missing_vec:
        print(f"  (dry-run: would upsert {len(missing_vec)} vectors)")

    # 5. delete ghost vectors
    removed = 0
    if ghost_vec and args.apply and not args.dry_run:
        ghost_list = sorted(ghost_vec)
        try:
            # batch delete (chromadb supports max ~1000 per call)
            for i in range(0, len(ghost_list), 500):
                col.delete(ids=ghost_list[i:i+500])
            removed = len(ghost_list)
            print(f"  ✓ deleted {removed} ghost vectors")
        except Exception as e:
            print(f"  ✗ batch delete failed: {e}")
    elif ghost_vec:
        print(f"  (dry-run: would delete {len(ghost_vec)} ghost vectors)")

    # 6. final verify
    if args.apply and not args.dry_run:
        got2 = col.get(include=[])
        chroma_after = set(got2.get("ids", [])) if got2 else set()
        remaining_missing = sql_uids - chroma_after
        remaining_ghost = chroma_after - sql_uids
        if not remaining_missing and not remaining_ghost:
            print("✓ reconciliation complete — SQLite ↔ ChromaDB now consistent")
        else:
            print(f"⚠ remaining: {len(remaining_missing)} missing, {len(remaining_ghost)} ghost")
            return 1

    conn.close()
    return 0


def _cmd_backup(args):
    """Create a point-in-time backup of the memory database."""
    import shutil
    db_path = os.environ.get("MEM_DB") or DEFAULT_DB
    if not os.path.isfile(db_path):
        print(f"✗ database not found: {db_path}")
        return 1
    import time as _time_mod
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = _time_mod.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"memory_{stamp}.db")
    # sqlite3 .backup API (safe even during writes with WAL)
    import sqlite3 as _sq
    src_conn = _sq.connect(db_path)
    dst_conn = _sq.connect(dest)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()
    sz = os.path.getsize(dest)
    print(f"✓ backup: {dest} ({sz/1024:.0f} KB)")
    # cleanup: keep last 7
    backups = sorted(f for f in os.listdir(backup_dir) if f.startswith("memory_") and f.endswith(".db"))
    if len(backups) > 7:
        for old in backups[:-7]:
            os.remove(os.path.join(backup_dir, old))
            print(f"  pruned old backup: {old}")
    return 0


def _cmd_correct(args):
    import gateway as _gw
    _gw.init_db()
    by = args.by or _gw.DEFAULT_SOURCE
    r = _gw.correct(args.uid, args.content, args.reason or 'corrected', by_agent=by,
                    valid_from=args.valid_from)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get('ok') else 1


def _cmd_retire(args):
    # guarded flow via governance.retire (dry-run default, residual-facts guard, --force)
    import gateway as _gw
    import governance as _gov
    _gw.init_db()  # ensure schema exists (governance assumes DB ready)
    _gov.DB = _gw.DB
    reason = args.reason or 'retired'
    if args.by:
        reason = f'{reason} (by {args.by})'
    r = _gov.retire(args.uid, reason=reason, apply=args.apply, force=args.force)
    if (not args.apply and not r.get('ok') is None and r.get('dry_run') and r.get('residual_warning')):
        # surface a clear hint on dry-run when --apply would be refused
        r['hint'] = 'apply will be refused unless --force (residual facts present)'
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get('ok') else 1


def _cmd_court(args):
    """Memory Court: evidence dossier for one memory + chain verification."""
    import sqlite3
    db_path = os.environ.get("MEM_DB") or DEFAULT_DB
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if args.verify:
        import memory_court as mc
        ok, detail = mc.verify_chain(conn)
        print("=" * 60)
        if ok:
            print(f"  MEMORY COURT: chain PASS - {detail.get('anchors',0)} anchor(s), "
                  f"{detail.get('entries_covered',0)} entries covered, "
                  f"{detail.get('unanchored_tail',0)} unanchored tail")
        else:
            print(f"  MEMORY COURT: chain FAIL - {detail.get('reason')}")
        print("=" * 60)
        conn.close()
        return 0 if ok else 1

    if args.export:
        import zipfile, json as _json
        import memory_court as mc
        uid_exp = args.uid
        if not uid_exp:
            print("usage: memtether court <uid> --export out.zip")
            conn.close()
            return 1
        fact = conn.execute("SELECT * FROM facts WHERE uid=?", (uid_exp,)).fetchone()
        if fact is None:
            print(f"no such memory: {uid_exp}")
            conn.close()
            return 1
        logs = conn.execute(
            "SELECT id, op, target, agent, detail, ts FROM audit_log "
            "WHERE target=? ORDER BY id", (uid_exp,)).fetchall()
        mc.maybe_anchor(conn, force=True)
        ok, detail = mc.verify_chain(conn)
        anchors = conn.execute(
            "SELECT id, last_log_id, chain_hash, entries_hashed, ts "
            "FROM audit_anchors ORDER BY id").fetchall()
        # case.json — machine-readable dossier
        case = {
            "uid": uid_exp,
            "content": fact["content"],
            "status": fact["status"],
            "confidence": fact["confidence"],
            "q_value": fact["q_value"],
            "use_count": fact["use_count"],
            "valid_from": fact["valid_from"], "valid_to": fact["valid_to"],
            "recorded_at": fact["recorded_at"], "invalidated_at": fact["invalidated_at"],
            "source": fact["source"],
            "audit_entries": [dict(zip(("id","op","target","agent","detail","ts"), r)) for r in logs],
            "anchors": [dict(zip(("id","last_log_id","chain_hash","entries_hashed","ts"), r)) for r in anchors],
            "chain_verified": ok,
        }
        # verify.html — self-verifying: recompute chain in browser JS
        chain_js = _json.dumps({
            "genesis": "GENESIS",
            "anchors": [dict(zip(("id","last_log_id","chain_hash","entries_hashed","ts"), r)) for r in anchors],
            "entries": [list(r) for r in logs],
        }, ensure_ascii=False)
        verify_html = '''<!DOCTYPE html><html><head><meta charset="utf-8"><title>MemTether Evidence</title>
<style>body{font-family:monospace;background:#0a0e17;color:#e8ecf4;padding:2em}
.ok{color:#4ade80}.bad{color:#ff5d6c}pre{white-space:pre-wrap}</style></head><body>
<h1>MemTether Evidence Chain — self-verifying report</h1>
<p>This page re-computes the sha256 hash chain locally in your browser.
No network, no dependencies. If the status below is PASS, the audit
records embedded here are exactly the records that were anchored.</p>
<div id="st">verifying…</div><pre id="d"></pre>
<script>
const data = %s;
async function sha256hex(s){
  const b = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('');
}
(async () => {
  let h = data.genesis; let checked = 0; let fail = null;
  for (const a of data.anchors) {
    for (const e of data.entries) {
      if (e[0] <= (data.anchors[data.anchors.indexOf(a)-1]?.last_log_id || 0)) continue;
      if (e[0] > a.last_log_id) continue;
      h = await sha256hex(h + e.join('|'));
      checked++;
    }
    if (h !== a.chain_hash) { fail = a; break; }
  }
  const st = document.getElementById('st');
  if (fail) { st.innerHTML = '<span class="bad">FAIL — anchor #'+fail.id+' hash mismatch</span>'; }
  else { st.innerHTML = '<span class="ok">PASS — '+data.anchors.length+' anchors, '+checked+' entries verified</span>'; }
  document.getElementById('d').textContent = JSON.stringify(data, null, 2);
})();
</script></body></html>''' % chain_js

        zpath = args.export
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("case.json", _json.dumps(case, ensure_ascii=False, indent=1))
            z.writestr("chain.jsonl",
                       "\n".join(_json.dumps(dict(zip(("id","op","target","agent","detail","ts"), r)), ensure_ascii=False) for r in logs))
            z.writestr("verify.html", verify_html)
            z.writestr("README.txt", (
                "MemTether Memory Court — evidence package\n"
                f"uid: {uid_exp}\n"
                f"chain_verified: {ok}\n\n"
                "How to verify:\n"
                "1. Open verify.html in any browser (works offline, no deps).\n"
                "   It re-computes the sha256 hash chain and shows PASS/FAIL.\n"
                "2. Or run: memtether court --verify  (on the source machine)\n"
                "3. case.json is the machine-readable dossier (EU AI Act Art.12).\n"))
        print(f"✓ evidence package: {zpath} (chain {'PASS' if ok else 'FAIL'})")
        conn.close()
        return 0

    uid = args.uid
    fact = conn.execute("SELECT * FROM facts WHERE uid=?", (uid,)).fetchone()
    if fact is None:
        print(f"no such memory: {uid}")
        conn.close()
        return 1

    W = 60
    def bar(c): return "=" * c
    print("+" + bar(W) + "+")
    print("| MEMTETHER MEMORY COURT - CASE FILE".ljust(W + 2) + "|")
    print("| UID: " + uid.ljust(W - 6) + "|")
    print("+" + bar(W) + "+")

    print("| 1. CONTENT (current active version)".ljust(W + 2) + "|")
    content = fact["content"] or ""
    for i in range(0, min(len(content), 180), W - 6):
        print("|   " + content[i:i+W-6].ljust(W - 4) + " |")
    print("|   status=%s conf=%.2f q=%.2f uses=%d" % (fact["status"], fact["confidence"], fact["q_value"], fact["use_count"]))
    print("|")
    print("| 2. TEMPORAL EVIDENCE (bi-temporal)".ljust(W + 2) + "|")
    print("|   valid (T):  %s -> %s" % (fact["valid_from"] or "?", fact["valid_to"] or "present"))
    print("|   known (T'): %s -> %s" % (fact["recorded_at"] or "?", fact["invalidated_at"] or "present"))
    print("|")
    print("| 3. PROVENANCE CHAIN (audit_log)".ljust(W + 2) + "|")
    logs = conn.execute(
        "SELECT id, op, agent, detail, ts FROM audit_log "
        "WHERE target=? ORDER BY id", (uid,)).fetchall()
    for lg in logs:
        d = (lg["detail"] or "")[:36]
        print("|   #%s %-9s by=%-12s %s" % (lg["id"], lg["op"], lg["agent"], d))
    if not logs:
        print("|   (no audit entries)".ljust(W + 2) + "|")
    print("|")
    print("| 4. CONFLICT ADJUDICATIONS".ljust(W + 2) + "|")
    crs = conn.execute(
        "SELECT id, uid_a, uid_b, verdict, by_agent FROM conflict_reviews "
        "WHERE uid_a=? OR uid_b=?", (uid, uid)).fetchall()
    if crs:
        for c in crs:
            other = c["uid_b"] if c["uid_a"] == uid else c["uid_a"]
            print("|   case #%d: vs %s... verdict=%s by=%s" % (c["id"], other[:20], c["verdict"], c["by_agent"]))
    else:
        print("|   (no conflicts on record)")
    print("|")
    print("| 5. INTEGRITY PROOF".ljust(W + 2) + "|")
    import memory_court as mc
    mc.maybe_anchor(conn, force=True)
    ok, detail = mc.verify_chain(conn)
    if ok:
        print("|   chain: PASS (%d anchors, %d entries hashed)" % (detail.get("anchors",0), detail.get("entries_covered",0)))
        print("|   verify anytime: memtether court --verify")
    else:
        print("|   chain: TAMPER DETECTED - see court --verify")
    print("+" + bar(W) + "+")
    conn.close()
    return 0


def _cmd_download_models(args):
    """Download local embedding models for semantic search (stdlib-only)."""
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "scripts", "download_models.py")
    if not os.path.exists(script):
        print("❌ scripts/download_models.py not found")
        return 1
    cmd = [sys.executable, script, "--profile", args.profile]
    if args.out:
        cmd.extend(["--out", args.out])
    if args.list:
        cmd.append("--list")
    return subprocess.run(cmd).returncode


def _cmd_import_docs(args):
    """Import a directory of docs (.md/.txt/.py) as memories — cold-start path."""
    import pathlib as _pl
    target = args.path
    if not os.path.isdir(target):
        print(f"Directory not found: {target}")
        return 1
    exts = {".md", ".txt", ".py", ".rst"}
    max_size = args.max_size
    imported = skipped = 0
    # P-TB2: skip vector sync for bulk imports (prevents model load hang)
    os.environ['MEM_SKIP_VECTOR'] = '1'
    try:
        pass
    finally:
        pass
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", "_dev", "build")]
        for f in files:
            fpath = os.path.join(root, f)
            ext = os.path.splitext(f)[1].lower()
            if ext not in exts:
                continue
            try:
                stat = os.stat(fpath)
                if stat.st_size > max_size or stat.st_size < 10:
                    skipped += 1
                    continue
                text = open(fpath, encoding="utf-8", errors="replace").read()
                # take first meaningful paragraph as memory content
                lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")][:15]
                if len(lines) < 2:
                    skipped += 1
                    continue
                summary = " ".join(lines).strip()[:500]
                if len(summary) < 20:
                    skipped += 1
                    continue
                rel = os.path.relpath(fpath, target)
                content = f"Imported from {rel}: {summary}"
                source = args.source or "doc_import"
                r = remember(content, type=args.type, source=source)
                if r and r.get("ok"):
                    imported += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1
    os.environ.pop('MEM_SKIP_VECTOR', None)
    print(f"Import complete: {imported} memories added, {skipped} skipped")
    return 0


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
    if getattr(args, "tenant", None):
        os.environ["MEM_TENANT_ID"] = args.tenant
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
    if getattr(args, "tenant", None):
        os.environ["MEM_TENANT_ID"] = args.tenant
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
    """Initialize a new memory DB (demo data) + auto-connect detected clients."""
    db_path = os.environ.get("MEM_DB") or DEFAULT_DB
    if os.path.exists(db_path) and not getattr(args, "force", False):
        print(f"DB already exists: {db_path}")
        print("Use --force to overwrite")
        return
    here = os.path.dirname(os.path.abspath(__file__))
    demo_script = os.path.join(here, "scripts", "make_demo_db.py")
    if os.path.exists(demo_script):
        r = subprocess.run([sys.executable, demo_script, "--out", db_path],
                          capture_output=True, text=True)
        if r.returncode == 0:
            print(f"✅ Created memory DB: {db_path}")
        else:
            print(f"❌ Failed to create DB: {r.stderr[:200]}")
    else:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        import sqlite3
        sqlite3.connect(db_path).close()
        print(f"✅ Created empty memory DB: {db_path}")

    # Auto-connect all detected clients (delegates to tether_connect).
    connect_script = os.path.join(here, "tether_connect.py")
    if os.path.exists(connect_script):
        print("\n[init] Connecting all detected AI clients...")
        subprocess.run([sys.executable, connect_script, "apply", "--yes"],
                       capture_output=False)

    print("[init] Done. Run 'memtether dashboard' to open the web UI.")
    print()
    print("💡 If MemTether helps you, please star: https://github.com/MemTether/MemTether")


def _cmd_connect(args):
    """Connect AI clients to the memory hub (delegates to tether_connect)."""
    here = os.path.dirname(os.path.abspath(__file__))
    connect_script = os.path.join(here, "tether_connect.py")
    if not os.path.exists(connect_script):
        print("❌ tether_connect.py not found")
        return
    cmd = [sys.executable, connect_script]
    if getattr(args, "client", None):
        cmd.extend(["apply", "--client", args.client])
    elif getattr(args, "all", False):
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
        fn = f"{typ}_{uid}.md"  # P2-6: full uid (30-char prefix collides)
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



def _cmd_provenance(args: argparse.Namespace) -> None:
    """Show the full provenance audit trail for a fact."""
    import sqlite3, json
    db_path = os.environ.get("MEM_DB", os.path.join(DATA_DIR, "memory.db"))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    uid = args.uid
    print(f"Provenance audit trail for: {uid}")
    print("=" * 60)

    # Get the fact itself
    cur.execute("SELECT uid, content, source, type, status, valid_from, valid_to, recorded_at, superseded_by, q_value FROM facts WHERE uid = ?", (uid,))
    fact = cur.fetchone()
    if not fact:
        print(f"Fact not found: {uid}")
        return

    print(f"  UID:          {fact['uid']}")
    print(f"  Content:      {fact['content'][:100]}...")
    print(f"  Source:       {fact['source']}")
    print(f"  Type:         {fact['type']}")
    print(f"  Status:       {fact['status']}")
    print(f"  Valid from:   {fact['valid_from']}")
    print(f"  Valid to:     {fact['valid_to'] or '(still active)'}")
    print(f"  Recorded at:  {fact['recorded_at']}")
    print(f"  Q-Value:      {fact['q_value']}")
    print(f"  Superseded by: {fact['superseded_by'] or '(none)'}")

    # Walk forward: what superseded this fact
    print(f"\n  Supersession chain (forward):")
    current = uid
    depth = 0
    while depth < 20:
        cur.execute("SELECT new_uid FROM supersessions WHERE old_uid = ?", (current,))
        row = cur.fetchone()
        if not row: break
        new_uid = row["new_uid"]
        cur.execute("SELECT ts, by_agent, reason FROM supersessions WHERE old_uid = ?", (current,))
        meta = cur.fetchone()
        ts = meta["ts"] if meta else "?"
        agent = meta["by_agent"] if meta else "?"
        reason = (meta["reason"] or "")[:60] if meta else ""
        print(f"    {depth+1}. {current} → {new_uid}")
        print(f"       at {ts} by {agent}: {reason}")
        current = new_uid
        depth += 1
    if depth == 0:
        print(f"    (no forward supersessions)")

    # Walk backward: what did this fact supersede
    print(f"\n  Supersession chain (backward):")
    current = uid
    depth = 0
    while depth < 20:
        cur.execute("SELECT old_uid FROM supersessions WHERE new_uid = ?", (current,))
        row = cur.fetchone()
        if not row: break
        old_uid = row["old_uid"]
        cur.execute("SELECT ts, by_agent FROM supersessions WHERE new_uid = ?", (current,))
        meta = cur.fetchone()
        ts = meta["ts"] if meta else "?"
        agent = meta["by_agent"] if meta else "?"
        print(f"    {depth+1}. {current} ← {old_uid}")
        print(f"       at {ts} by {agent}")
        current = old_uid
        depth += 1
    if depth == 0:
        print(f"    (no backward supersessions)")

    conn.close()
    print("=" * 60)
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
    sp.add_argument("--tenant", default=None, help="Tenant ID filter")
    sp.add_argument("--list", action="store_true",
                    help="L0 mode: return one-line summaries + UIDs instead of full content")
    sp.set_defaults(func=_cmd_search)

    sp = sub.add_parser("remember", help="写入一条记忆（必须带 source 归属）")
    sp.add_argument("content")
    sp.add_argument("--type", default="fact",
                    help="fact/experience/decision/incident/todo")
    sp.add_argument("--source", default="memtether")
    sp.add_argument("--tenant", default=None, help="Tenant ID (env MEM_TENANT_ID overrides)")
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

    sp_dash = sub.add_parser("dashboard", help="Start the web dashboard (API server + browser UI)")
    sp_dash.add_argument("--port", type=int, default=8820, help="Port to run on (default 8820)")
    sp_dash.set_defaults(func=_cmd_dashboard)

    sp_cf = sub.add_parser("conflicts", help="Review conflict-detection candidates (governance loop)")
    sp_cf.add_argument("--stats", action="store_true", help="Show counts by verdict")
    sp_cf.add_argument("--list", action="store_true", help="List pending pairs with content preview")
    sp_cf.add_argument("--limit", type=int, default=20, help="Max pairs to list")
    sp_cf.add_argument("--accept", default=None, help="Comma-separated ids: confirm genuine conflict")
    sp_cf.add_argument("--discard", default=None, help="Comma-separated ids: mark no_conflict")
    sp_cf.add_argument("--note", default="", help="Optional review note")
    sp_cf.add_argument("--by", default="", help="Reviewer agent/source name")
    sp_cf.set_defaults(func=_cmd_conflicts)

    sp_rb = sub.add_parser("remember-batch", help="Batch write memories from a JSON file")
    sp_rb.add_argument("file", help="JSON array of {content, type, source} objects")
    sp_rb.set_defaults(func=_cmd_remember_batch)
    sp_bk = sub.add_parser("backup", help="Create a point-in-time backup of the memory database")
    sp_bk.set_defaults(func=_cmd_backup)
    sp_rc = sub.add_parser("reconcile", help="Fix SQLite ↔ ChromaDB vector store inconsistencies (incremental rebuild)")
    sp_rc.add_argument("--dry-run", action="store_true", help="report without fixing")
    sp_rc.add_argument("--apply", action="store_true", help="apply fixes (upsert missing + delete ghosts). Required to mutate.")
    sp_rc.set_defaults(func=_cmd_reconcile)
    sp_co = sub.add_parser("correct", help="Correct a memory: supersede old content with new (chain, no delete)")
    sp_co.add_argument("uid", help="UID of the memory to correct")
    sp_co.add_argument("content", help="New content")
    sp_co.add_argument("--reason", default="", help="Why this correction")
    sp_co.add_argument("--by", default="", help="Agent/source performing the correction")
    sp_co.add_argument("--valid-from", dest="valid_from", default=None, help="T-axis start of the new fact")
    sp_co.set_defaults(func=_cmd_correct)

    sp_re = sub.add_parser("retire", help="Retire a memory (mark superseded; data is kept)")
    sp_re.add_argument("uid", help="UID of the memory to retire")
    sp_re.add_argument("--reason", default="", help="Why retiring")
    sp_re.add_argument("--by", default="", help="Agent/source performing the retirement")
    sp_re.add_argument("--apply", action="store_true", help="Actually apply (default: dry-run)")
    sp_re.add_argument("--force", action="store_true", help="Override residual-facts guard")
    sp_re.set_defaults(func=_cmd_retire)

    sp_ct = sub.add_parser("court", help="Memory Court: evidence dossier / hash-chain verify")
    sp_ct.add_argument("uid", nargs="?", default=None)
    sp_ct.add_argument("--verify", action="store_true")
    sp_ct.add_argument("--export", metavar="ZIP", default=None)
    sp_ct.set_defaults(func=_cmd_court)
    sp_dl = sub.add_parser("download-models", help="Download local embedding models for semantic search (bge-small-zh ~46MB / bge-m3-int8 ~560MB)")
    sp_dl.add_argument("--profile", default="bge-m3-int8", help="Model profile (bge-small-zh or bge-m3-int8)")
    sp_dl.add_argument("--out", default=None, help="Target models dir (default: <repo>/models or MEM_MODELS_DIR)")
    sp_dl.add_argument("--list", action="store_true", help="List available profiles")
    sp_dl.set_defaults(func=_cmd_download_models)

    sp_import = sub.add_parser("import-docs", help="Import a directory of docs as memories (cold-start)")
    sp_import.add_argument("path", help="Directory to scan")
    sp_import.add_argument("--source", default="doc_import", help="Source tag")
    sp_import.add_argument("--type", default="fact", help="Memory type")
    sp_import.add_argument("--max-size", type=int, default=100000, help="Max file size in bytes")
    sp_import.set_defaults(func=_cmd_import_docs)
    sp = sub.add_parser("provenance", help="Show full audit trail for a fact (supersession chain + source + timestamps)")
    sp.add_argument("uid", help="Fact UID to trace")
    sp.set_defaults(func=_cmd_provenance)

    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())


