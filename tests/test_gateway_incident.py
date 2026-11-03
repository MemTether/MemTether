# -*- coding: utf-8 -*-
"""tests/test_gateway_incident.py — incident cooldown + severity tiering (P4, 2026-11-03).

Two goals, both locked against regressions:
1) Same-signature incident retries collapse into the existing row via
   incident_cooldown audit + hit:N tag. This must happen BEFORE near-dup
   matching, otherwise dedup hides the retry and hit counts never move.
2) Non-fatal incidents are downgraded to experience; fatal keywords keep
   type=incident. The severity check must not run for cooldown hits.
"""
import os, sys, sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'


def _setup(tmp_path):
    db = str(tmp_path / 'inc.db')
    os.environ['MEM_DB'] = db
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()
    return db, gateway


def _row(db, uid):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    r = conn.execute('SELECT uid, type, content, tags FROM facts WHERE uid=?', (uid,)).fetchone()
    conn.close()
    return r


def _audit_has(db, op):
    conn = sqlite3.connect(db)
    n = conn.execute('SELECT COUNT(*) FROM audit_log WHERE op=?', (op,)).fetchone()[0]
    conn.close()
    return n


def test_exact_dup_incident_still_counts_hit(tmp_path):
    """The original bug: exact dup returned noop_dup before cooldown ran,
    so repeated real-world retries never produced hit:N."""
    db, gw = _setup(tmp_path)
    c = 'incident retry: Alpha API data loss corruption incident'
    r1 = gw.remember(c, type='incident', source='codex')
    assert r1['op'] == 'insert', r1
    r2 = gw.remember(c, type='incident', source='codex')
    assert r2['op'] == 'cooldown_increment', r2
    assert r2['count'] == 1, r2
    row = _row(db, r1['uid'])
    assert row['type'] == 'incident'
    assert 'hit:1' in row['tags']
    assert _audit_has(db, 'incident_cooldown') == 1


def test_paraphrase_retry_uses_one_row_and_increments_hit(tmp_path):
    db, gw = _setup(tmp_path)
    c1 = 'incident retry: Beta gateway corruption caused 30s timeout'
    c2 = 'incident retry: Beta gateway corruption caused 30 seconds timeout'
    r1 = gw.remember(c1, type='incident', source='codex')
    assert r1['op'] == 'insert'
    r2 = gw.remember(c2, type='incident', source='codex')
    assert r2['op'] == 'cooldown_increment', r2
    assert r2['uid'] == r1['uid']
    row = _row(db, r1['uid'])
    assert 'hit:1' in row['tags']
    assert _audit_has(db, 'incident_cooldown') == 1


def test_non_fatal_incident_downgrades_to_experience(tmp_path):
    db, gw = _setup(tmp_path)
    r = gw.remember('minor incident: search was a bit slow today', type='incident', source='codex')
    row = _row(db, r['uid'])
    assert row['type'] == 'experience', row


def test_fatal_incident_keeps_type(tmp_path):
    db, gw = _setup(tmp_path)
    r = gw.remember('fatal incident: data loss corruption security incident', type='incident', source='codex')
    row = _row(db, r['uid'])
    assert row['type'] == 'incident', row
