# -*- coding: utf-8 -*-
"""tests/test_cli_correct_retire.py — new `correct` and `retire` CLI subcommands.

Black-box audit (2026-10-08) found gateway.correct()/governance.retire() had no
CLI surface: pip-installed users could never call them without writing Python.
These tests drive the real CLI via subprocess (same path users take).
"""
import os, sys, subprocess, sqlite3, tempfile
import pytest

@pytest.fixture
def cli_env(tmp_path):
    db = str(tmp_path / 'cli.db')
    env = dict(os.environ)
    env['MEM_DB'] = db
    env['MEM_SKIP_VECTOR'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    py = sys.executable
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    def run(args):
        return subprocess.run([py, '-m', 'memtether'] + args, capture_output=True,
                              timeout=120, env=env, cwd=cwd)
    # seed one fact
    run(['remember', 'cli correct retire probe single fact only', '--source', 'bbtest'])
    conn = sqlite3.connect(db)
    uid = conn.execute("SELECT uid FROM facts WHERE content LIKE 'cli correct retire probe%'").fetchone()[0]
    conn.close()
    return run, uid, db

def test_correct_supersedes(cli_env):
    run, uid, db = cli_env
    r = run(['correct', uid, 'corrected via cli', '--reason', 'test', '--by', 'bbtest'])
    assert r.returncode == 0
    assert b'"op": "supersede"' in r.stdout or b'new_uid' in r.stdout
    conn = sqlite3.connect(db)
    old = conn.execute('SELECT status FROM facts WHERE uid=?', (uid,)).fetchone()[0]
    n = conn.execute('SELECT COUNT(*) FROM facts WHERE content=?', ('corrected via cli',)).fetchone()[0]
    conn.close()
    assert old == 'superseded' and n == 1

def test_retire_dryrun_then_apply(cli_env):
    run, uid, db = cli_env
    r1 = run(['retire', uid, '--reason', 'dry'])
    assert r1.returncode == 0
    conn = sqlite3.connect(db)
    st = conn.execute('SELECT status FROM facts WHERE uid=?', (uid,)).fetchone()[0]
    conn.close()
    assert st == 'active'  # dry-run does not mutate
    # without conflict context, every sentence counts as residual -> --apply is
    # refused by design (guard). --force is the documented override.
    r2 = run(['retire', uid, '--reason', 'apply now', '--apply'])
    assert r2.returncode == 1
    r3 = run(['retire', uid, '--reason', 'apply now', '--apply', '--force'])
    assert r3.returncode == 0
    conn = sqlite3.connect(db)
    st = conn.execute('SELECT status FROM facts WHERE uid=?', (uid,)).fetchone()[0]
    conn.close()
    assert st == 'superseded'

def test_retire_residual_guard_blocks_apply(tmp_path):
    """Content with independent residual facts must refuse --apply without --force."""
    import gateway as gw
    db = str(tmp_path / 'g.db')
    os.environ['MEM_DB'] = db
    import importlib
    importlib.reload(gw)
    gw.init_db()
    r = gw.remember('torch 2.13 是坏的；但是价格是每百万 1 元这个信息还是独立的有效内容很长', source='bbtest')
    run = cli_env.__wrapped__ if hasattr(cli_env, '__wrapped__') else None
    env = dict(os.environ); env['MEM_DB'] = db; env['MEM_SKIP_VECTOR'] = '1'
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = subprocess.run([sys.executable, '-m', 'memtether', 'retire', r['uid'],
                        '--reason', 'torch issue', '--apply'],
                       capture_output=True, timeout=120, env=env, cwd=cwd)
    assert p.returncode == 1  # refused: residual facts present
    out = p.stdout.decode('utf-8', 'replace')
    assert 'residual' in out.lower() or '残留' in out
    # --force overrides
    p2 = subprocess.run([sys.executable, '-m', 'memtether', 'retire', r['uid'],
                         '--reason', 'torch issue', '--apply', '--force'],
                        capture_output=True, timeout=120, env=env, cwd=cwd)
    assert p2.returncode == 0
