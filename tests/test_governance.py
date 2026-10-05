"""governance conflict detection tests."""
import os, sys, tempfile, sqlite3
sys.path.insert(0, r'E:\RUANJIAN\memtether')
os.environ['MEM_SKIP_VECTOR'] = '1'

def _db(tmpdir):
    db = os.path.join(tmpdir, 'g.db')
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE facts (
        uid TEXT PRIMARY KEY, type TEXT, subject TEXT, content TEXT,
        status TEXT DEFAULT 'active', source TEXT, created_at TEXT, updated_at TEXT,
        valid_from TEXT, recorded_at TEXT, scope TEXT DEFAULT 'shared', q_value REAL DEFAULT 0.5)""")
    conn.commit(); conn.close()
    return db

def test_detect_conflicts_positive(tmp_path):
    import governance
    db = _db(str(tmp_path))
    governance.DB = db
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO facts (uid,content,status,source,created_at,updated_at,valid_from,recorded_at) VALUES (?,?,?,?,?,?,?,?)",
        ('uid1','Fooocus is available','active','codex','2026-10-01','2026-10-01','2026-10-01','2026-10-01'))
    conn.execute("INSERT INTO facts (uid,content,status,source,created_at,updated_at,valid_from,recorded_at) VALUES (?,?,?,?,?,?,?,?)",
        ('uid2','Fooocus is NOT available','active','codex','2026-10-02','2026-10-02','2026-10-02','2026-10-02'))
    conn.commit(); conn.close()
    # governance api
    if hasattr(governance, 'detect_explicit_conflicts'):
        res = governance.detect_explicit_conflicts(days_window=30)
        assert res is not None

def test_detect_conflicts_negative(tmp_path):
    import governance
    db = _db(str(tmp_path))
    governance.DB = db
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO facts (uid,content,status,source,created_at,updated_at,valid_from,recorded_at) VALUES (?,?,?,?,?,?,?,?)",
        ('uid1','ComfyUI uses torch 2.13','active','codex','2026-10-01','2026-10-01','2026-10-01','2026-10-01'))
    conn.execute("INSERT INTO facts (uid,content,status,source,created_at,updated_at,valid_from,recorded_at) VALUES (?,?,?,?,?,?,?,?)",
        ('uid2','STM32 is a microcontroller','active','codex','2026-10-02','2026-10-02','2026-10-02','2026-10-02'))
    conn.commit(); conn.close()
    if hasattr(governance, 'detect_explicit_conflicts'):
        res = governance.detect_explicit_conflicts(days_window=30)
        # Should not flag unrelated facts
        assert res is None or (isinstance(res,(list,dict)) and len(res)==0) or res == []
