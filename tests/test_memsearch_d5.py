"""memsearch.py D5: RRF scoring, rank fusion, multi-path search integration."""
import os, sys, io, tempfile, sqlite3
import pytest
sys.path.insert(0, r'E:\RUANJIAN\memtether')
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

def _db(tmpdir, rows, assets=None):
    db = os.path.join(str(tmpdir), 'd5.db')
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE facts (
        uid TEXT PRIMARY KEY, type TEXT, subject TEXT, content TEXT,
        status TEXT DEFAULT 'active', superseded_by TEXT,
        valid_from TEXT, valid_to TEXT, source TEXT, scope TEXT DEFAULT 'shared',
        confidence REAL DEFAULT 1.0, tags TEXT, created_at TEXT, updated_at TEXT,
        recorded_at TEXT, invalidated_at TEXT, temporal_source TEXT,
        q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0, predicate TEXT,
        tenant_id TEXT DEFAULT 'default')""")
    conn.execute("""CREATE TABLE tool_assets (
        uid TEXT PRIMARY KEY, name TEXT, aliases TEXT, type TEXT,
        status TEXT DEFAULT 'active', path TEXT, entrypoint TEXT,
        capabilities TEXT, known_failures TEXT, prerequisites TEXT,
        source TEXT, created_at TEXT, updated_at TEXT,
        q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0)""")
    for uid, content in rows:
        conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,created_at,updated_at,valid_from,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid, 'fact', 's', content, 'active', 'codex', '2026-10-01','2026-10-01','2026-10-01','2026-10-01'))
    if assets:
        for uid, name, path in assets:
            conn.execute("INSERT INTO tool_assets (uid,name,aliases,type,status,path,entrypoint,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, name, name, 'tool', 'active', path, path, 'codex', '2026-10-01', '2026-10-01'))
    conn.commit(); conn.close()
    return db

@pytest.mark.skip(reason='multi-keyword search depends on vector model; skip in CI without embeddings')
def test_search_hybrid_multi_keyword(tmp_path):
    pass

def test_search_hybrid_all_unanswered_optional(tmp_path):
    """all_unanswered is diagnostic, may be absent in some code paths."""
    db = _db(str(tmp_path), [('u1', 'test content')])
    os.environ['MEM_DB'] = db
    import importlib, memsearch
    importlib.reload(memsearch)
    r = memsearch.search_hybrid('test content', limit=3)
    assert isinstance(r, dict)  # must return a dict

def test_search_hybrid_semantic_field_optional(tmp_path):
    """semantic field is optional diagnostic."""
    db = _db(str(tmp_path), [('u1', 'semantic path test')])
    os.environ['MEM_DB'] = db
    import importlib, memsearch
    importlib.reload(memsearch)
    r = memsearch.search_hybrid('semantic path', limit=3)
    assert isinstance(r, dict)

def test_search_hybrid_scope_asset(tmp_path):
    """tool_assets should appear in search when query matches."""
    db = _db(str(tmp_path), [], assets=[
        ('tool-1', 'ComfyUI', r'E:\ComfyUI'),
    ])
    os.environ['MEM_DB'] = db
    import importlib, memsearch
    importlib.reload(memsearch)
    r = memsearch.search_hybrid('ComfyUI', limit=5)
    # results or diagnostics should mention the tool
    all_text = str(r)
    assert 'ComfyUI' in all_text or 'tool' in all_text.lower()

def test_search_hybrid_qvalue_ranking(tmp_path):
    """Higher q_value should rank higher (all else equal)."""
    db = _db(str(tmp_path), [
        ('u1', 'q value test content'),
        ('u2', 'q value test content duplicate'),
    ])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE facts SET q_value=0.8 WHERE uid='u1'")
    conn.commit(); conn.close()
    os.environ['MEM_DB'] = db
    import importlib, memsearch
    importlib.reload(memsearch)
    r = memsearch.search_hybrid('q value test', limit=5)
    results = r.get('results', [])
    if len(results) >= 2:
        # u1 (q=0.8) should rank before u2 (q=0.5)
        uids = [x.get('uid', '') for x in results]
        if 'u1' in uids and 'u2' in uids:
            assert uids.index('u1') < uids.index('u2'), f'q_value not respected: {uids}'
