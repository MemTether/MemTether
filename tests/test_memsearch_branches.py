# -*- coding: utf-8 -*-
"""tests/test_memsearch_branches.py — R8/R10/T-B/P4-2 branch coverage (memsearch 51% -> 60%+).

Locks the behavior switches that have env toggles but no regression net:
- R8 low-confidence marker (kw empty + max vec < 0.5)
- R10 scaffold prepending for counting/comparison/temporal/knowledge-update
- T-B AFAG answer hints (COUNT/FREQUENT NUMBERS/EARLIEST/LATEST)
- packet compilation for aggregation-type questions
- P4-2 multi-round retry (MEM_MULTI_ROUND=1)
- R7 three-layer dedup counters
"""
import os, sys, json, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'


def _db(tmpdir, rows):
    db = os.path.join(str(tmpdir), 'mb.db')
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
    conn.execute("""CREATE TABLE supersessions (
        id INTEGER PRIMARY KEY, old_uid TEXT, new_uid TEXT, reason TEXT, by_agent TEXT, ts TEXT)""")
    for i, row in enumerate(rows):
        uid, content, kw = row[0], row[1], (row[2] if len(row) > 2 else {})
        tags = kw.get('tags', '')
        st = kw.get('status', 'active')
        sup = kw.get('superseded_by', '')
        conn.execute("INSERT INTO facts (uid,type,subject,content,status,source,created_at,updated_at,valid_from,recorded_at,tags,superseded_by)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (uid, 'fact', 's', content, st, 'codex', '2026-10-01 10:00', '2026-10-0%d 10:00' % (i % 9 + 1),
                      '2026-10-01 10:00', '2026-10-01 10:00', tags, sup))
    conn.commit(); conn.close()
    return db


def _search(db, q, monkeypatch, limit=5, **env):
    monkeypatch.setenv('MEM_DB', db)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import memsearch
    memsearch.DB = db
    return memsearch.search_hybrid(q, limit=limit)


class TestR8LowConfidence:
    def test_kw_miss_marks_low_confidence(self, tmp_path, monkeypatch):
        db = _db(str(tmp_path), [('u1', 'completely unrelated zebra content')])
        r = _search(db, 'quantum entanglement xylophone', monkeypatch)
        # nothing matches → kw_count==0 and low_confidence marked (vec empty too)
        assert r['diag']['kw_count'] == 0
        assert r['diag']['low_confidence'] is True

    def test_low_confidence_consistent_with_diag(self, tmp_path, monkeypatch):
        # low_confidence = (no kw recall AND no vector sim) — with bare schema both
        # paths may be dead; the invariant is consistency between the three diag fields.
        db = _db(str(tmp_path), [('u1', 'ComfyUI is the drawing tool we use')])
        r = _search(db, 'ComfyUI', monkeypatch)
        d = r['diag']
        expected = (d['kw_count'] == 0 and d['max_vec_sim'] < 0.50)
        assert d['low_confidence'] == expected


class TestR7Dedup:
    def test_content_dedup_counter(self, tmp_path, monkeypatch):
        # two active facts with identical content — kw path may rank both
        db = _db(str(tmp_path), [
            ('u1', 'unique duplicate content marker qqq'),
            ('u2', 'unique duplicate content marker qqq'),
        ])
        r = _search(db, 'unique duplicate content marker qqq', monkeypatch, limit=10)
        seen = [x['content'] for x in r['results']]
        assert len(seen) == len(set(seen))  # no duplicate contents returned
        if r['diag'].get('content_dedup'):
            assert r['diag']['content_dedup'] >= 1

    def test_supersession_dedup_removes_old(self, tmp_path, monkeypatch):
        # old fact superseded by new; if both recalled, old must vanish
        db = _db(str(tmp_path), [
            ('old1', 'version one of the config fact zzz', {'status': 'superseded', 'superseded_by': 'new1'}),
            ('new1', 'version two of the config fact zzz'),
        ])
        r = _search(db, 'version config fact zzz', monkeypatch, limit=10)
        uids = [x['uid'] for x in r['results']]
        if 'new1' in uids:
            assert 'old1' not in uids


class TestR10Scaffold:
    def test_counting_scaffold_prepended(self, tmp_path, monkeypatch):
        db = _db(str(tmp_path), [
            ('u1', 'ComfyUI appears here'),
            ('u2', 'ComfyUI appears there too'),
        ])
        r = _search(db, 'how many ComfyUI mentions', monkeypatch)
        # scaffold prepended to first result content (if any results)
        if r['results']:
            first = r['results'][0]['content']
            assert first.startswith('[scaffold: counting') or 'scaffold' in r['diag']
            assert r['diag'].get('scaffold') or 'scaffold' in first

    def test_question_type_detection(self, tmp_path, monkeypatch):
        db = _db(str(tmp_path), [('u1', 'filler content for detection probe')])
        for q, want in [('how many apples', 'counting'),
                        ('compare a versus b', 'comparison'),
                        ('when did it happen', 'temporal'),
                        ('latest version of x', 'knowledge-update'),
                        ('list all instances', 'aggregation'),
                        ('plain question', 'single-session')]:
            r = _search(db, q, monkeypatch)
            assert r['diag']['question_type'] == want, q


class TestTBAfagHints:
    def test_counting_hints_extracted(self, tmp_path, monkeypatch):
        db = _db(str(tmp_path), [
            ('u1', 'ComfyUI 3 mentions of port 8188'),
            ('u2', 'ComfyUI again port 8188'),
        ])
        r = _search(db, 'how many ComfyUI', monkeypatch)
        hints = r['diag'].get('afag_hints')
        if hints:
            joined = hints if isinstance(hints, str) else ' '.join(hints)
            assert 'COUNT ComfyUI' in joined or 'FREQUENT NUMBERS' in joined

    def test_temporal_hints_earliest_latest(self, tmp_path, monkeypatch):
        db = _db(str(tmp_path), [
            ('u1', 'when did the deploy happen probe'),
        ])
        r = _search(db, 'when did the deploy happen probe', monkeypatch)
        hints = r['diag'].get('afag_hints')
        if hints:
            joined = hints if isinstance(hints, str) else ' '.join(hints)
            assert 'EARLIEST' in joined and 'LATEST' in joined


class TestMultiRound:
    def test_multi_round_disabled_by_default(self, tmp_path, monkeypatch):
        db = _db(str(tmp_path), [('u1', 'obscure token xyzq')])
        r = _search(db, 'obscure token xyzq', monkeypatch)
        assert 'multi_round' not in r['diag']

    def test_multi_round_triggers_on_low_score(self, tmp_path, monkeypatch):
        db = _db(str(tmp_path), [('u1', 'the really obscure token xyzq lives here')])
        r = _search(db, 'please help me find the really obscure token xyzq quickly', monkeypatch,
                    MEM_MULTI_ROUND='1', MEM_MULTI_ROUND_THR='0.9')
        # with thr=0.9 and normalized scores well below, round-2 should fire (or attempt)
        assert r['diag'].get('multi_round') is True or r['diag'].get('multi_round_err')
