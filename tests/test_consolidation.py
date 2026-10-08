# -*- coding: utf-8 -*-
"""tests/test_consolidation.py — consolidation.py coverage (was 25%, only import-line coverage).

v19 misjudged this as a "dead module": memsearch.py:1161 lazily calls
search_index() (R10b, try/except ImportError guarded). Deleting it would
silently drop the consolidation recall path. These tests lock its behavior.

2026-10-08 user decision B: build_topic_timelines() was REMOVED — it JOINed
a fact_entities table that no shipped schema creates (would crash on real DB).
Shipped index types: value histories, standing instructions. A regression test
locks the removal.
"""
import os, sys, json, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'

import consolidation


def _real_schema_db(tmpdir):
    """facts + supersessions with the exact columns gateway.py:281 creates."""
    db = os.path.join(str(tmpdir), 'c.db')
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE,
        type TEXT, subject TEXT, content TEXT NOT NULL,
        status TEXT DEFAULT 'active', superseded_by TEXT,
        valid_from TEXT, valid_to TEXT, recorded_at TEXT, invalidated_at TEXT,
        temporal_source TEXT, source TEXT, scope TEXT DEFAULT 'shared',
        confidence REAL DEFAULT 0.8, tags TEXT, created_at TEXT, updated_at TEXT,
        q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE supersessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, old_uid TEXT, new_uid TEXT,
        reason TEXT, by_agent TEXT, ts TEXT)""")
    rows = [
        ('u1', 'Always check proxy before git push to github'),
        ('u2', 'Never delete the production memory.db file'),
        ('u3', 'ComfyUI uses torch 2.13 nightly build'),
    ]
    for uid, content in rows:
        conn.execute(
            "INSERT INTO facts (uid, content, status, source, created_at, updated_at, valid_from, recorded_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (uid, content, 'active', 'codex', '2026-10-01 10:00',
             '2026-10-01 10:00', '2026-10-01 10:00', '2026-10-01 10:00'))
    conn.commit(); conn.close()
    return db


def test_build_standing_instructions_finds_patterns(tmp_path, monkeypatch):
    db = _real_schema_db(tmp_path)
    monkeypatch.setattr(consolidation, 'HERE', str(tmp_path))
    monkeypatch.setenv('MEM_DB', db)
    res = consolidation.build_standing_instructions()
    # u1 ('always') and u2 ('never') match the LIKE patterns; u3 does not
    uids = {i['uid'] for i in json.load(open(os.path.join(str(tmp_path), 'standing_instructions.json'), encoding='utf-8'))}
    assert 'u1' in uids and 'u2' in uids and 'u3' not in uids


def test_build_value_histories_follows_supersession_chains(tmp_path, monkeypatch):
    db = _real_schema_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO facts (uid, content, status, source, created_at, updated_at, valid_from, recorded_at)"
                 " VALUES ('u1new','ComfyUI uses torch 2.14 stable','superseded','codex','2026-10-02 10:00','2026-10-02 10:00','2026-10-02 10:00','2026-10-02 10:00')")
    conn.execute("INSERT INTO supersessions (old_uid,new_uid,reason,by_agent,ts) VALUES ('u3','u1new','version bump','codex','2026-10-02 10:00')")
    conn.commit(); conn.close()
    monkeypatch.setattr(consolidation, 'HERE', str(tmp_path))
    monkeypatch.setenv('MEM_DB', db)
    res = consolidation.build_value_histories()
    assert res['value_chains'] >= 1
    hist = json.load(open(os.path.join(str(tmp_path), 'value_history.json'), encoding='utf-8'))
    key = [k for k in hist if 'torch 2.13' in k][0]
    assert 'torch 2.14' in hist[key][0]['new']


def test_search_index_standing_instruction(tmp_path, monkeypatch):
    monkeypatch.setattr(consolidation, 'HERE', str(tmp_path))
    insts = [{'uid': 'u1', 'content': 'Always check proxy before git push', 'type': 'decision',
              'source': 'codex', 'ts': '2026-10-01 10:00', 'trigger': 'always'}]
    json.dump(insts, open(os.path.join(str(tmp_path), 'standing_instructions.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    res = consolidation.search_index('always', limit=5)
    types = {r['type'] for r in res}
    assert 'standing_instruction' in types
    si = [r for r in res if r['type'] == 'standing_instruction'][0]
    assert si['content'] == 'Always check proxy before git push'


def test_topic_timelines_removed_regression(tmp_path, monkeypatch):
    """User decision B (2026-10-08): topic timelines removed — it JOINed a
    fact_entities table no schema creates. Locks: function absent, build_all
    has no topics key, search_index never emits topic_timeline type."""
    assert not hasattr(consolidation, 'build_topic_timelines')
    monkeypatch.setattr(consolidation, 'HERE', str(tmp_path))
    regress = str(tmp_path / 'regress.db')
    monkeypatch.setenv('MEM_DB', regress)
    import sqlite3 as _sq3
    _c = _sq3.connect(regress)
    _c.execute("CREATE TABLE facts (uid TEXT PRIMARY KEY, content TEXT, status TEXT, type TEXT, source TEXT, created_at TEXT, updated_at TEXT)")
    _c.execute("CREATE TABLE supersessions (id INTEGER PRIMARY KEY, old_uid TEXT, new_uid TEXT, reason TEXT, by_agent TEXT, ts TEXT)")
    _c.commit(); _c.close()
    res = consolidation.build_all()
    assert 'topics' not in res and 'values' in res and 'instructions' in res
    # even a stale topic_index.json on disk must not resurrect the path
    json.dump({'x': {'count': 1, 'timeline': []}},
              open(os.path.join(str(tmp_path), 'topic_index.json'), 'w', encoding='utf-8'))
    assert not any(r['type'] == 'topic_timeline' for r in consolidation.search_index('x'))


def test_search_index_empty_when_no_index_files(tmp_path, monkeypatch):
    monkeypatch.setattr(consolidation, 'HERE', str(tmp_path))
    assert consolidation.search_index('anything') == []
