# -*- coding: utf-8 -*-
"""Regression tests: read-side audit log (opt-in) + obsolete-family diag.

Paper anchors:
- arXiv 2610.36308 (Obsolete Facts in Agent Memory): split "retrieval missed
  the newest version" from "agent saw both versions" -> our diag exposes
  stale_annotated_n + obsolete_family_n.
- ROADMAP mid-term read-side audit + arXiv 2610.25707 (owner attribution):
  opt-in MEM_READ_LOG=1 records who-read-what with hash-chain anchoring.
"""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'


def _setup(tmp_path, monkeypatch, read_log='0'):
    db = str(tmp_path / 'audit.db')
    # Isolate EVERYTHING earlier tests may have leaked: tenant, scope, read-log,
    # projection env. monkeypatch auto-restores at test end.
    for k in ('MEM_TENANT_ID', 'MEM_SCOPE', 'MEM_READ_LOG', 'MEM_READ_AGENT',
              'MEM_SINK_PATH', 'MEM_PROJ_PATH', 'MEM_PROJ_BUDGET'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('MEM_DB', db)
    monkeypatch.setenv('MEM_SKIP_VECTOR', '1')
    if read_log == '1':
        monkeypatch.setenv('MEM_READ_LOG', '1')
        monkeypatch.setenv('MEM_READ_AGENT', 'codex-test')
    import importlib, memsearch
    importlib.reload(memsearch)
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE facts (uid TEXT PRIMARY KEY, type TEXT, subject TEXT, content TEXT,
          status TEXT DEFAULT 'active', superseded_by TEXT, valid_from TEXT,
          valid_to TEXT, recorded_at TEXT, invalidated_at TEXT, temporal_source TEXT,
          source TEXT, scope TEXT DEFAULT 'shared', confidence REAL DEFAULT 0.8,
          tags TEXT, created_at TEXT, updated_at TEXT, q_value REAL DEFAULT 0.5,
          use_count INTEGER DEFAULT 0, predicate TEXT, tenant_id TEXT DEFAULT 'default',
          verify_at TEXT, last_verified_at TEXT);
        CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, op TEXT, target TEXT,
          agent TEXT, detail TEXT, ts TEXT);
    """)
    # newest version + its superseded predecessor both active-visible? No:
    # superseded predecessor stays status='active' with superseded_by set
    # (that is exactly the "agent saw BOTH versions" family case).
    conn.execute("INSERT INTO facts (uid,type,content,status,source,recorded_at,created_at,superseded_by) VALUES"
                 "('u-old','fact','alpha configuration uses port A','active','codex','2026-11-05','2026-11-05','u-new')")
    conn.execute("INSERT INTO facts (uid,type,content,status,source,recorded_at,created_at) VALUES"
                 "('u-new','fact','alpha configuration uses port B','active','codex','2026-11-05','2026-11-05')")
    conn.commit(); conn.close()
    return memsearch, db


def test_read_log_opt_in_records_and_anchors(tmp_path, monkeypatch):
    ms, db = _setup(tmp_path, monkeypatch, read_log='1')
    res = ms.search_hybrid('alpha configuration', limit=5)
    assert res['results'], res
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT uid, agent FROM read_log").fetchall()
    anchors = conn.execute("SELECT COUNT(*) FROM audit_anchors").fetchone()[0]
    conn.close()
    uids = {r[0] for r in rows}
    assert 'u-new' in uids, 'returned hits must be logged'
    assert all(r[1] == 'codex-test' for r in rows)
    assert anchors >= 1, 'read_log must feed the audit hash chain'


def test_read_log_off_by_default(tmp_path, monkeypatch):
    ms, db = _setup(tmp_path, monkeypatch, read_log='0')
    ms.search_hybrid('alpha configuration', limit=5)
    conn = sqlite3.connect(db)
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE name='read_log'").fetchone()
    conn.close()
    assert not has, 'read_log table must not exist when opt-in is off'


def test_obsolete_family_diag(tmp_path, monkeypatch):
    """2610.36308 mapping: R7 dedup removes the superseded predecessor, so the
    healthy final result is newest-only; the diag must show the removal count
    and the family invariant must stay 0 (both-versions-visible = regression).
    """
    ms, db = _setup(tmp_path, monkeypatch)
    res = ms.search_hybrid('alpha configuration port', limit=5)
    d = res['diag']
    assert d.get('supersession_dedup', 0) >= 1, \
        'old version should be removed when newest is present'
    uids = [r['uid'] for r in res['results']]
    assert 'u-old' not in uids and 'u-new' in uids
    assert d.get('obsolete_family_n') == 0, \
        'both versions visible = R7 dedup invariant violated'
