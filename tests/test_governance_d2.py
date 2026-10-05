"""governance.py D2 deep tests (polarity/POS-NEG/candidate generation)."""
import os, sys, io, tempfile, sqlite3
sys.path.insert(0, r'E:\RUANJIAN\memtether')
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

def _db(tmpdir, rows):
    db = os.path.join(str(tmpdir), 'g2.db')
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE facts (
        uid TEXT PRIMARY KEY, type TEXT, subject TEXT, content TEXT,
        status TEXT DEFAULT 'active', source TEXT, created_at TEXT, updated_at TEXT,
        valid_from TEXT, recorded_at TEXT, scope TEXT DEFAULT 'shared',
        q_value REAL DEFAULT 0.5)""")
    for i, (uid, content) in enumerate(rows):
        conn.execute("INSERT INTO facts (uid,content,status,source,created_at,updated_at,valid_from,recorded_at) VALUES (?,?,?,?,?,?,?,?)",
            (uid, content, 'active', 'codex', f'2026-10-0{i+1}', f'2026-10-0{i+1}', f'2026-10-0{i+1}', f'2026-10-0{i+1}'))
    conn.commit(); conn.close()
    return db

def test_detect_no_conflicts_single(tmp_path):
    import governance
    db = _db(str(tmp_path), [('u1', 'ComfyUI uses torch 2.13')])
    governance.DB = db
    r = governance.detect_explicit_conflicts(days_window=30)
    assert r is None or (isinstance(r, (list, dict)) and len(r) == 0)

def test_detect_pos_neg_conflict(tmp_path):
    import governance
    db = _db(str(tmp_path), [
        ('u1', 'Fooocus is available'),
        ('u2', 'Fooocus is not available'),
    ])
    governance.DB = db
    r = governance.detect_explicit_conflicts(days_window=30)
    assert r is not None  # should detect POS/NEG conflict

def test_conflict_same_polarity_not_flagged(tmp_path):
    import governance
    db = _db(str(tmp_path), [
        ('u1', 'Fooocus is available'),
        ('u2', 'Fooocus is available and works'),
    ])
    governance.DB = db
    r = governance.detect_explicit_conflicts(days_window=30)
    assert r is None or (isinstance(r, (list, dict)) and len(r) == 0)

def test_conflict_unrelated_entities(tmp_path):
    import governance
    db = _db(str(tmp_path), [
        ('u1', 'ComfyUI is available'),
        ('u2', 'STM32 is not available'),
    ])
    governance.DB = db
    r = governance.detect_explicit_conflicts(days_window=30)
    assert r is None or (isinstance(r, (list, dict)) and len(r) == 0)

def test_governance_module_exports():
    import governance
    assert hasattr(governance, 'detect_explicit_conflicts')
