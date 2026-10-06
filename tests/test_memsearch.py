"""Core search_hybrid tests (P1-1 coverage)."""
import os, sys, io, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

def _make_db(tmpdir):
    db = os.path.join(tmpdir, 't.db')
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE facts (
        uid TEXT PRIMARY KEY, type TEXT, subject TEXT, content TEXT,
        status TEXT DEFAULT 'active', superseded_by TEXT,
        valid_from TEXT, valid_to TEXT, source TEXT, scope TEXT DEFAULT 'shared',
        confidence REAL DEFAULT 1.0, tags TEXT, created_at TEXT, updated_at TEXT,
        recorded_at TEXT, invalidated_at TEXT, temporal_source TEXT,
        q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0, predicate TEXT,
        tenant_id TEXT DEFAULT 'default')""")
    conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,created_at,updated_at,valid_from,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ('uid1','fact','s1','ComfyUI is a drawing tool with torch2.13.0+cu130','active','codex','2026-10-01','2026-10-01','2026-10-01','2026-10-01'))
    conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,created_at,updated_at,valid_from,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ('uid2','fact','s2','STM32 is a microcontroller used for embedded development','active','codex','2026-10-02','2026-10-02','2026-10-02','2026-10-02'))
    conn.commit(); conn.close()
    return db

def test_search_hybrid_keyword_hit(tmp_path):
    db = _make_db(str(tmp_path))
    os.environ['MEM_DB'] = db
    import importlib, memsearch
    importlib.reload(memsearch)
    r = memsearch.search_hybrid('ComfyUI', limit=5)
    assert r and 'results' in r
    texts = [x.get('content','') for x in r['results']]
    assert any('ComfyUI' in t for t in texts), f'expected ComfyUI, got {texts}'

def test_search_hybrid_no_cross_hit(tmp_path):
    db = _make_db(str(tmp_path))
    os.environ['MEM_DB'] = db
    import importlib, memsearch
    importlib.reload(memsearch)
    r = memsearch.search_hybrid('STM32', limit=5)
    assert r and 'results' in r
    texts = [x.get('content','') for x in r['results']]
    assert any('STM32' in t for t in texts), f'expected STM32, got {texts}'
    assert not any('ComfyUI is a drawing' in t for t in texts)

def test_scope_filter_excludes_private(tmp_path):
    db = _make_db(str(tmp_path))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE facts SET scope='private' WHERE uid='uid2'")
    conn.commit(); conn.close()
    os.environ['MEM_DB'] = db
    os.environ.pop('MEM_SCOPE', None)
    import importlib, memsearch
    importlib.reload(memsearch)
    r = memsearch.search_hybrid('STM32', limit=5)
    texts = [x.get('content','') for x in r.get('results',[])]
    assert not any('STM32' in t for t in texts), f'private leaked: {texts}'
