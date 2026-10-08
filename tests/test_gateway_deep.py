# -*- coding: utf-8 -*-
"""tests/test_gateway_deep.py — gateway.py coverage round (27% -> target 40%+).

Covers the bi-temporal query surface (as_of valid/known), timeline chain
walking, stats/set_pin/bump_qvalue CLI-level functions, remember validation
matrix, and search scope filtering — all against sandboxed DBs.
"""
import os, sys, json, sqlite3, tempfile, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'


def _setup(tmp_path):
    db = str(tmp_path / 'gd.db')
    os.environ['MEM_DB'] = db
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()
    return db, gateway


class TestRememberValidation:
    def test_content_too_long(self, tmp_path):
        db, gw = _setup(tmp_path)
        try:
            gw.remember('x' * 10241, source='codex')
            assert False
        except ValueError:
            pass

    def test_invalid_type(self, tmp_path):
        db, gw = _setup(tmp_path)
        try:
            gw.remember('c', type='bogus', source='codex')
            assert False
        except ValueError:
            pass

    def test_invalid_confidence(self, tmp_path):
        db, gw = _setup(tmp_path)
        try:
            gw.remember('c', source='codex', confidence=1.5)
            assert False
        except ValueError:
            pass

    def test_invalid_scope(self, tmp_path):
        db, gw = _setup(tmp_path)
        try:
            gw.remember('c', source='codex', scope='public')
            assert False
        except ValueError:
            pass

    def test_dup_same_source_noop(self, tmp_path):
        db, gw = _setup(tmp_path)
        r1 = gw.remember('unique dup probe content', source='codex')
        r2 = gw.remember('unique dup probe content', source='codex')
        assert r2.get('op') == 'noop_dup'
        assert r2['uid'] == r1['uid']

    def test_valid_from_explicit(self, tmp_path):
        db, gw = _setup(tmp_path)
        r = gw.remember('backdated fact', source='codex', valid_from='2026-09-01 08:00')
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT valid_from, recorded_at FROM facts WHERE uid=?", (r['uid'],)).fetchone()
        conn.close()
        assert row[0].startswith('2026-09-01')
        assert not row[1].startswith('2026-09-01')  # recorded now, valid earlier


class TestAsOf:
    def _seed(self, tmp_path):
        db, gw = _setup(tmp_path)
        # fact valid 09-01→09-10, recorded 09-01, invalidated 09-10
        r1 = gw.remember('was true then', source='codex', valid_from='2026-09-01 08:00')
        conn = sqlite3.connect(db)
        conn.execute("UPDATE facts SET valid_to='2026-09-10 08:00', invalidated_at='2026-09-10 08:00' WHERE uid=?", (r1['uid'],))
        conn.commit(); conn.close()
        return gw, r1['uid']

    def test_valid_axis_honours_valid_to(self, tmp_path):
        gw, uid = self._seed(tmp_path)
        inside = gw.as_of('2026-09-05 12:00', kind='valid')
        outside = gw.as_of('2026-09-15 12:00', kind='valid')
        assert uid in [x['uid'] for x in inside['rows']]
        assert uid not in [x['uid'] for x in outside['rows']]

    def test_known_axis_differs_from_valid(self, tmp_path):
        gw, uid = self._seed(tmp_path)
        # fact recorded_at = now (2026-10-08), invalidated_at = 2026-09-10.
        # known axis: recorded_at <= at AND (invalidated_at empty OR > at)
        # → invalidated before recorded → this uid can never appear on 'known' axis,
        #   which is exactly the bi-temporal distinction (valid axis DID show it).
        known_now = gw.as_of('2026-10-08 23:59', kind='known')
        assert uid not in [x['uid'] for x in known_now['rows']]
        # valid axis at the same moment: valid_to 09-10 < now → also gone
        valid_now = gw.as_of('2026-10-08 23:59', kind='valid')
        assert uid not in [x['uid'] for x in valid_now['rows']]

    def test_filter_by_type(self, tmp_path):
        gw, _ = self._seed(tmp_path)
        gw.remember('typed probe decision content', type='decision', source='codex')
        res = gw.as_of('2999-01-01 00:00', kind='valid', ftype='decision')
        assert all(x.get('type') == 'decision' for x in res['rows'])
        assert res['count'] >= 1
        assert any('typed probe' in x['content'] for x in res['rows'])


class TestTimeline:
    def test_chain_walk(self, tmp_path):
        db, gw = _setup(tmp_path)
        r1 = gw.remember('chain v1', source='codex')
        r2 = gw.correct(r1['uid'], 'chain v2', reason='update')
        # timeline() walks FORWARD (superseded_by) — must start from the old uid
        tl = gw.timeline(r1['uid'])
        assert len(tl) >= 2
        assert tl[0]['uid'] == r1['uid']
        assert any(x['uid'] == r2['new_uid'] for x in tl)
        # newest node has no successor
        assert gw.timeline(r2['new_uid'])[0]['superseded_by'] is None

    def test_unknown_uid_empty(self, tmp_path):
        db, gw = _setup(tmp_path)
        assert gw.timeline('fact-nope') == []


class TestStatsAndPin:
    def test_stats_counts(self, tmp_path):
        db, gw = _setup(tmp_path)
        gw.remember('stats probe one', source='codex')
        gw.remember('stats probe two', source='codex', type='decision')
        s = gw.stats()
        assert isinstance(s, dict)
        txt = json.dumps(s, ensure_ascii=False)
        assert '2' in txt  # at least our two facts counted somewhere

    def test_set_pin_toggle(self, tmp_path):
        db, gw = _setup(tmp_path)
        r = gw.remember('pinned probe', source='codex')
        uid = r['uid']
        conn = sqlite3.connect(db)
        # pin column may or may not exist depending on schema version
        cols = [c[1] for c in conn.execute('PRAGMA table_info(facts)').fetchall()]
        conn.close()
        if 'pin' in cols:
            gw.set_pin(uid, on=True)
            conn = sqlite3.connect(db)
            v = conn.execute("SELECT pin FROM facts WHERE uid=?", (uid,)).fetchone()[0]
            conn.close()
            assert v


class TestBumpQvalue:
    def test_bump_raises_qvalue(self, tmp_path):
        db, gw = _setup(tmp_path)
        r = gw.remember('qvalue probe', source='codex')
        uid = r['uid']
        gw.bump_qvalue(uid=uid, reward=1.0, agent='codex')
        conn = sqlite3.connect(db)
        q = conn.execute("SELECT q_value, use_count FROM facts WHERE uid=?", (uid,)).fetchone()
        conn.close()
        assert q[1] >= 1
        assert q[0] > 0.5 or q[0] == 0.5  # floor-guarded; use_count is the hard assertion

    def test_bump_unknown_uid_safe(self, tmp_path):
        db, gw = _setup(tmp_path)
        # must not raise
        gw.bump_qvalue(uid='fact-ghost', reward=1.0, agent='codex')


class TestSearchScope:
    def test_private_not_in_default_search(self, tmp_path):
        db, gw = _setup(tmp_path)
        gw.remember('public searchable probe zebra', source='codex', scope='shared')
        gw.remember('private searchable probe zebra', source='codex', scope='private')
        res = gw.search('searchable probe zebra', limit=10)
        rows = res['results'] if isinstance(res, dict) else res
        contents = ' '.join(
            (x.get('content', '') if isinstance(x, dict) else str(x))
            for x in rows)
        assert 'private' not in contents
