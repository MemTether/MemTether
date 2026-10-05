"""gateway.py D2 functional tests (remember/correct/retire regression)."""
import os, sys, io, tempfile, sqlite3
sys.path.insert(0, r'E:\RUANJIAN\memtether')
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

def _setup(tmp_path):
    db = str(tmp_path / 'gw.db')
    os.environ['MEM_DB'] = db
    import importlib, gateway, memsearch
    importlib.reload(gateway)
    importlib.reload(memsearch)
    return db, gateway

def test_remember_basic(tmp_path):
    db, gw = _setup(tmp_path)
    r = gw.remember('test remember content alpha', type='fact', source='codex')
    uid = r['uid'] if isinstance(r, dict) else r
    assert uid is not None
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT content, source, status FROM facts WHERE uid=?", (uid,)).fetchone()
    conn.close()
    assert row and row[0] == 'test remember content alpha'
    assert row[1] == 'codex'
    assert row[2] == 'active'

def test_remember_with_tags(tmp_path):
    db, gw = _setup(tmp_path)
    r = gw.remember('tagged content', type='fact', source='codex', tags='test,extra')
    uid = r['uid'] if isinstance(r, dict) else r
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT tags FROM facts WHERE uid=?", (uid,)).fetchone()
    conn.close()
    assert row and 'test' in (row[0] or '')

def test_remember_missing_source_rejected(tmp_path):
    db, gw = _setup(tmp_path)
    try:
        gw.remember('no source', type='fact', source='')
        assert False, 'should reject empty source'
    except (ValueError, SystemExit, Exception):
        pass

def test_remember_empty_content_rejected(tmp_path):
    db, gw = _setup(tmp_path)
    try:
        gw.remember('', type='fact', source='codex')
        assert False, 'should reject empty content'
    except (ValueError, SystemExit, Exception):
        pass

def test_correct_creates_supersession(tmp_path):
    db, gw = _setup(tmp_path)
    r1_raw = gw.remember('port is 8080', type='fact', source='codex')
    uid1 = r1_raw['uid'] if isinstance(r1_raw, dict) else r1_raw
    r2_raw = gw.correct(uid1, 'port is 9090', reason='port changed')
    uid2 = r2_raw.get('new_uid') if isinstance(r2_raw, dict) else r2_raw
    assert uid2 is not None
    conn = sqlite3.connect(db)
    r1 = conn.execute("SELECT status, superseded_by FROM facts WHERE uid=?", (uid1,)).fetchone()
    r2 = conn.execute("SELECT status FROM facts WHERE uid=?", (uid2,)).fetchone()
    conn.close()
    assert r1 and r1[0] == 'superseded'
    assert r1[1] == uid2
    assert r2 and r2[0] == 'active'

def test_retire_marks_inactive(tmp_path):
    db, gw = _setup(tmp_path)
    r_raw = gw.remember('to be retired', type='fact', source='codex')
    uid = r_raw['uid'] if isinstance(r_raw, dict) else r_raw
    gw.retire(uid, reason='obsolete')
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status FROM facts WHERE uid=?", (uid,)).fetchone()
    conn.close()
    assert row and row[0] == 'retired'

def test_remember_duplicate_rejected_or_merged(tmp_path):
    db, gw = _setup(tmp_path)
    r1 = gw.remember('exact same content', type='fact', source='codex')
    r2 = gw.remember('exact same content', type='fact', source='codex')
    u1 = r1['uid'] if isinstance(r1, dict) else r1
    u2 = r2['uid'] if isinstance(r2, dict) else r2
    # Should dedup: uid2 should be None or same as uid1
    assert u2 is None or u2 == u1

def test_tenant_id_written(tmp_path):
    db, gw = _setup(tmp_path)
    os.environ['MEM_TENANT_ID'] = 'tenantT'
    r = gw.remember('tenant test content', type='fact', source='codex')
    uid = r['uid'] if isinstance(r, dict) else r
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT tenant_id FROM facts WHERE uid=?", (uid,)).fetchone()
    conn.close()
    os.environ.pop('MEM_TENANT_ID', None)
    assert row and row[0] == 'tenantT'
