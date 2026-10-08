# -*- coding: utf-8 -*-
"""tests/test_fresh_db.py — every CLI command must handle a brand-new (empty/nonexistent) DB.

Black-box audit round 4 (2026-10-08): `memtether search x` on a fresh install
(before any remember/init) crashed with "no such table: facts" — the very
first command a new user runs. Fixed by init_db in _cmd_search; this suite
locks ALL commands against fresh-DB crashes.
"""
import os, sys, subprocess, json
import pytest

CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _fresh_env(tmp_path):
    db = str(tmp_path / 'fresh.db')
    if os.path.exists(db):
        os.remove(db)
    env = dict(os.environ)
    env['MEM_DB'] = db
    env['MEM_SKIP_VECTOR'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env, db

CMDS = [
    ['search', 'anything'],
    ['stats'],
    ['conflicts', '--stats'],
    ['court', '--verify'],
    ['correct', 'fact-nope', 'new content', '--reason', 'x'],
    ['retire', 'fact-nope', '--reason', 'x'],
    ['court', 'fact-nope'],
    ['provenance', 'fact-nope'],
]

@pytest.mark.parametrize("cmd", CMDS, ids=[c[0] for c in CMDS])
def test_fresh_db_no_crash(tmp_path, cmd):
    env, db = _fresh_env(tmp_path)
    r = subprocess.run([sys.executable, '-m', 'memtether'] + cmd,
                       capture_output=True, timeout=120, env=env, cwd=CWD)
    out = (r.stdout or b'').decode('utf-8', 'replace') + (r.stderr or b'').decode('utf-8', 'replace')
    assert 'Traceback' not in out, f'{cmd} crashed on fresh DB:\n{out[:400]}'

def test_fresh_db_search_graceful(tmp_path):
    env, db = _fresh_env(tmp_path)
    r = subprocess.run([sys.executable, '-m', 'memtether', 'search', 'anything'],
                       capture_output=True, timeout=120, env=env, cwd=CWD)
    assert r.returncode == 0
    assert '无结果' in r.stdout.decode('utf-8', 'replace')
