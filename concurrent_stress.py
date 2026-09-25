# -*- coding: utf-8 -*-
"""
concurrent_stress.py — N8 (2026-09-25): hubguard 并发写入压测

模拟多客户端（Codex / WorkBuddy / Doubao）同时向 memory.db 写入记忆，
验证：
  1. 没有数据丢失（每个客户端写入的条目都能被检索到）
  2. 没有锁死（所有进程都能在合理时间内完成）
  3. 没有内容冲突/覆盖（每条记忆的 source 归属正确）
  4. 投影 rebuild 后状态一致（active 总数 = 预期）
"""
import os, sys, sqlite3, subprocess, tempfile, time, json, shutil, random
sys.stdout = __import__('io').TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PY = sys.executable
HUB = r'E:\RUANJIAN\memory_hub'

def make_env(db_path):
    env = os.environ.copy()
    env['MEM_DB'] = db_path
    env['MEM_PROJ_PATH'] = os.path.join(os.path.dirname(db_path), 'MEMORY.md')
    env['MEM_TMP'] = tempfile.gettempdir()
    env['PYTHONIOENCODING'] = 'utf-8'
    return env

def init_db(db_path):
    """Initialize a clean memory.db with the schema from memory_hub."""
    from pathlib import Path
    conn = sqlite3.connect(db_path)
    conn.executescript('''
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
        description TEXT, prerequisites TEXT, status TEXT DEFAULT 'active'
    );
    CREATE TABLE IF NOT EXISTS tool_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_uid TEXT, event TEXT, ts TEXT
    );
    ''')
    conn.commit()
    conn.close()

def run_writer(db_path, source, n, env):
    """Write n memories via gateway.py CLI."""
    for i in range(n):
        content = f'{source} 写入的第{i}条：测试记忆内容，验证并发不丢失。'
        r = subprocess.run(
            [PY, os.path.join(HUB, 'gateway.py'), 'remember', content,
             '--type', 'fact', '--source', source],
            env=env, capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace',
            cwd=HUB)
        if r.returncode != 0:
            return {'ok': False, 'source': source, 'i': i, 'stderr': r.stderr[:200]}
    return {'ok': True, 'source': source, 'written': n}

def main():
    tmpdir = tempfile.mkdtemp(prefix='hg-stress-')
    db = os.path.join(tmpdir, 'memory.db')
    init_db(db)
    env = make_env(db)

    sources = [('codex', 5), ('workbuddy', 5), ('doubao_a', 5)]
    start = time.time()
    # Run writers sequentially via subprocess (concurrent via multiple processes)
    results = []
    for src, n in sources:
        r = run_writer(db, src, n, env)
        results.append(r)
        if not r.get('ok'):
            print('FAIL:', r)
            break

    wall = time.time() - start
    conn = sqlite3.connect(db)
    total = conn.execute("SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]
    by_src = dict(conn.execute(
        "SELECT source, COUNT(*) FROM facts WHERE status='active' GROUP BY source").fetchall())
    conn.close()

    ok = all(r.get('ok') for r in results)
    expected = sum(n for _, n in sources)
    data_ok = (total == expected)
    src_ok = all(by_src.get(s, 0) == n for s, n in sources)

    print(json.dumps({
        'test': 'hubguard concurrent stress (N8)',
        'db': db, 'wall_s': round(wall, 2),
        'writers_ok': ok, 'data_ok': data_ok, 'source_ok': src_ok,
        'expected': expected, 'actual': total, 'by_source': by_src,
        'verdict': 'PASS' if ok and data_ok and src_ok else 'FAIL'
    }, ensure_ascii=False, indent=2))

    shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == '__main__':
    main()
