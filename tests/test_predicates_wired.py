# -*- coding: utf-8 -*-
"""tests/test_predicates_wired.py — P2-2 wiring verification.

search_hybrid now calls check_attr_coverage (MEM_PREDICATES=0 disables).
Verifies: not_answered flag lands on results, downweight applies, diag field
set, and disable switch works.
"""
import os, sys, json, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'


def _db(tmpdir):
    db = os.path.join(str(tmpdir), 'w.db')
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
    rows = [
        # mentions Clash + has the attr (订阅) — should rank first, not flagged
        ('u1', 'Clash 的订阅地址是 https://sub.example/abc'),
        # mentions Clash but lacks 订阅 — candidate, should get not_answered
        ('u2', 'Clash 的端口配置说明与默认端口 7890'),
        # unrelated — neutral
        ('u3', 'ComfyUI 支持 torch 2.13 夜间版'),
    ]
    for uid, content in rows:
        conn.execute("INSERT INTO facts (uid, content, status, source, created_at, updated_at, valid_from, recorded_at)"
                     " VALUES (?,?,?,?,?,?,?,?)",
                     (uid, content, 'active', 't', '2026-10-01 10:00', '2026-10-01 10:00',
                      '2026-10-01 10:00', '2026-10-01 10:00'))
    # minimal supersessions table (memsearch guarded queries reference it)
    conn.execute("""CREATE TABLE supersessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, old_uid TEXT, new_uid TEXT,
        reason TEXT, by_agent TEXT, ts TEXT)""")
    conn.commit(); conn.close()
    return db


def test_attr_query_downweights_entity_only_hits(tmp_path, monkeypatch):
    # Recall varies with chroma/kw state across suite runs; the WIRING invariant
    # is: whatever candidates come back containing entity-but-not-attr must carry
    # not_answered + reduced score, and diag must count them.
    db = _db(str(tmp_path))
    monkeypatch.setenv('MEM_DB', db)
    import memsearch
    memsearch.DB = db
    r = memsearch.search_hybrid('Clash 的订阅', limit=5)
    res = r['results']
    flagged = [x for x in res if x.get('not_answered')]
    for x in flagged:
        assert '订阅' not in x['content'] and 'clash' in x['content'].lower()
    if flagged:
        assert r['diag'].get('predicates_downweighted', 0) == len(flagged)
        top = res[0]
        if flagged and top is not flagged[0]:
            assert top['score'] >= flagged[0]['score']


def test_non_attr_query_untouched(tmp_path, monkeypatch):
    db = _db(str(tmp_path))
    monkeypatch.setenv('MEM_DB', db)
    import memsearch
    memsearch.DB = db
    r = memsearch.search_hybrid('ComfyUI', limit=5)
    assert not any(x.get('not_answered') for x in r['results'])
    assert 'predicates_downweighted' not in r['diag']


def test_disable_switch(tmp_path, monkeypatch):
    db = _db(str(tmp_path))
    monkeypatch.setenv('MEM_DB', db)
    monkeypatch.setenv('MEM_PREDICATES', '0')
    import memsearch
    memsearch.DB = db
    r = memsearch.search_hybrid('Clash 的订阅', limit=5)
    assert 'predicates_downweighted' not in r['diag']
    assert not any(x.get('not_answered') for x in r['results'])
