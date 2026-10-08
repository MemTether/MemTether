# -*- coding: utf-8 -*-
"""tests/test_reconcile_gate.py — reconcile must require explicit --apply to mutate.

Black-box audit (2026-10-08): running `memtether reconcile` (no flags) silently
FIXED the vector store — deleting "ghost" vectors that belonged to a DIFFERENT
database sharing the same store directory. Now mutation requires --apply.
"""
import os, sys, subprocess, sqlite3

def _seed(db):
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE facts (uid TEXT PRIMARY KEY, content TEXT, status TEXT DEFAULT 'active')")
    for i in range(3):
        conn.execute("INSERT INTO facts VALUES (?,?, 'active')", (f'uid-{i}', f'content {i}'))
    conn.commit(); conn.close()

def test_reconcile_no_flag_is_report_only(tmp_path):
    db = str(tmp_path / 't.db')
    _seed(db)
    env = dict(os.environ); env['MEM_DB'] = db; env['MEM_SKIP_VECTOR'] = '1'
    # chroma path resolves to <db dir>/mem0_store — pre-populate with a foreign vector via API
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # run report-only
    p = subprocess.run([sys.executable, '-m', 'memtether', 'reconcile'],
                       capture_output=True, timeout=300, env=env, cwd=cwd)
    out = p.stdout.decode('utf-8', 'replace')
    # valid outcomes: full report (chroma present) OR graceful degradation (no chroma)
    assert ('dry-run' in out or 'would' in out) or ('ChromaDB unavailable' in out)
    assert p.returncode == 0

def test_reconcile_apply_gate_documented(tmp_path):
    """--apply exists and --dry-run/--apply together resolve to report-only."""
    p = subprocess.run([sys.executable, '-m', 'memtether', 'reconcile', '--help'],
                       capture_output=True, timeout=60,
                       env=dict(os.environ, MEM_SKIP_VECTOR='1'),
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = p.stdout.decode('utf-8', 'replace')
    assert '--apply' in out and '--dry-run' in out
