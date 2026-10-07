# -*- coding: utf-8 -*-
"""tests/test_consolidation.py — consolidation.py coverage (was 25%, only import-line coverage).

v19 misjudged this as a "dead module": memsearch.py:1161 lazily calls
search_index() (R10b, try/except ImportError guarded). Deleting it would
silently drop the consolidation recall path. These tests lock its behavior.

Honest scope: build_topic_timelines() JOINs fact_entities — a table that
does NOT exist in any schema this repo ships (gateway.py creates facts /
supersessions / tool_assets only). That function would crash on a real DB.
We do NOT test it here; we test the two index builders that work against
the real schema, plus search_index() end-to-end.
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


def test_search_index_matches_topic_and_instruction(tmp_path, monkeypatch):
    monkeypatch.setattr(consolidation, 'HERE', str(tmp_path))
    topics = {'comfyui': {'count': 3, 'first_seen': '2026-10-01', 'last_seen': '2026-10-05',
                           'timeline': [{'uid': 'u3', 'ts': '2026-10-05', 'type': 'fact', 'content': 'torch 2.13'}]}}
    json.dump(topics, open(os.path.join(str(tmp_path), 'topic_index.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    insts = [{'uid': 'u1', 'content': 'Always check proxy before git push', 'type': 'decision',
              'source': 'codex', 'ts': '2026-10-01 10:00', 'trigger': 'always'}]
    json.dump(insts, open(os.path.join(str(tmp_path), 'standing_instructions.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    res = consolidation.search_index('comfyui always', limit=5)
    types = {r['type'] for r in res}
    assert 'topic_timeline' in types and 'standing_instruction' in types
    tt = [r for r in res if r['type'] == 'topic_timeline'][0]
    assert tt['topic'] == 'comfyui' and tt['count'] == 3


def test_search_index_empty_when_no_index_files(tmp_path, monkeypatch):
    monkeypatch.setattr(consolidation, 'HERE', str(tmp_path))
    assert consolidation.search_index('anything') == []
