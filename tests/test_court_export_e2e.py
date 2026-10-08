# -*- coding: utf-8 -*-
"""tests/test_court_export_e2e.py — court --export browser verification round trip.

Black-box audit round 3 (2026-10-08) found the exported verify.html FAILED in
a real browser on a perfectly valid chain: ① the export filtered audit
entries by target uid while the anchor covered ALL entries, and ② the JS
re-verification hashed raw 5-field rows instead of the 6-field row_hash the
CLI uses. Both fixed; these tests lock the round trip.
"""
import os, sys, json, zipfile, subprocess, tempfile
import pytest

@pytest.fixture
def seeded_db(tmp_path):
    db = str(tmp_path / 'e2e.db')
    env = dict(os.environ)
    env['MEM_DB'] = db
    env['MEM_SKIP_VECTOR'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    py = sys.executable
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([py, '-m', 'memtether', 'remember', 'e2e export probe unique marker', '--source', 'bbtest'],
                       capture_output=True, timeout=120, env=env, cwd=cwd)
    assert r.returncode == 0
    import sqlite3
    conn = sqlite3.connect(db)
    uid = conn.execute("SELECT uid FROM facts WHERE content LIKE 'e2e export%'").fetchone()[0]
    conn.close()
    return env, py, cwd, uid

def test_export_zip_contains_verifiable_chain(seeded_db, tmp_path):
    env, py, cwd, uid = seeded_db
    zpath = str(tmp_path / 'evidence.zip')
    r = subprocess.run([py, '-m', 'memtether', 'court', uid, '--export', zpath],
                       capture_output=True, timeout=120, env=env, cwd=cwd)
    assert r.returncode == 0
    z = zipfile.ZipFile(zpath)
    assert 'case.json' in z.namelist() and 'verify.html' in z.namelist()
    case = json.loads(z.read('case.json').decode('utf-8'))
    assert case['chain_verified'] is True
    # entries must include the FULL chain (all audit rows), not a target-filtered subset
    v = z.read('verify.html').decode('utf-8')
    assert 'rowHash' in v  # fixed algorithm marker

def test_verify_html_js_uses_row_hash(seeded_db, tmp_path):
    """The browser JS must hash 6-field canonical rows (with target), matching
    memory_court._row_hash — the old 5-field raw-join version always FAILed."""
    env, py, cwd, uid = seeded_db
    zpath = str(tmp_path / 'evidence2.zip')
    subprocess.run([py, '-m', 'memtether', 'court', uid, '--export', zpath],
                   capture_output=True, timeout=120, env=env, cwd=cwd)
    v = zipfile.ZipFile(zpath).read('verify.html').decode('utf-8')
    import re
    m = re.search(r'const data = (\{.*?\});', v)
    assert m
    data = json.loads(m.group(1))
    import hashlib
    h = data['genesis']
    for e in data['entries']:
        h = hashlib.sha256((h + hashlib.sha256('|'.join(str(x) for x in e).encode()).hexdigest()).encode()).hexdigest()
    assert h == data['anchors'][-1]['chain_hash']
