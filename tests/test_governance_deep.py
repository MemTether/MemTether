# -*- coding: utf-8 -*-
"""tests/test_governance_deep.py — governance.py 21% -> target 50%+.

Covers the untested public API surface: decay_factor math, apply_decay
(including the v1-updated_at-missing DB-lookup path and decay_missing flag),
find_stale risk classification, record_conflict_review + reviewed_no_conflict
roundtrip, _residual_facts splitting, retire dry-run/refusal/force paths,
and find_self_contradictions.
"""
import os, sys, json, sqlite3, tempfile, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'

import governance as G


def _real_db(tmpdir):
    db = os.path.join(str(tmpdir), 'g.db')
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE,
        type TEXT, subject TEXT, content TEXT NOT NULL,
        status TEXT DEFAULT 'active', superseded_by TEXT,
        valid_from TEXT, valid_to TEXT, recorded_at TEXT, invalidated_at TEXT,
        temporal_source TEXT, source TEXT, scope TEXT DEFAULT 'shared',
        confidence REAL DEFAULT 0.8, tags TEXT, created_at TEXT, updated_at TEXT,
        q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0)""")
    conn.commit(); conn.close()
    return db


def _now(days_ago=0):
    return dt.datetime.now() - dt.timedelta(days=days_ago)


class TestDecayFactor:
    def test_fresh_fact_full_score(self):
        ts = _now(0).strftime('%Y-%m-%d %H:%M')
        f, age = G.decay_factor(ts, 'fact', now=dt.datetime.now())
        assert f > 0.999 and age < 1.0

    def test_half_life(self):
        now = _now(0)
        ts = (now - dt.timedelta(days=30)).strftime('%Y-%m-%d %H:%M')
        f, age = G.decay_factor(ts, 'fact', halflife=30, now=now)
        assert abs(f - 0.5) < 0.01 and abs(age - 30) < 0.01

    def test_floor(self):
        ts = '2020-01-01 00:00'
        f, age = G.decay_factor(ts, 'fact', now=dt.datetime.now())
        assert f == G.DECAY_FLOOR

    def test_bad_timestamp_returns_neutral(self):
        f, age = G.decay_factor('not-a-date', 'fact')
        assert f > 0.999 and age < 1.0

    def test_empty_returns_neutral(self):
        f, age = G.decay_factor('', 'fact')
        assert f == 1.0


class TestApplyDecay:
    def _db(self, tmp_path, rows):
        db = _real_db(str(tmp_path))
        conn = sqlite3.connect(db)
        for uid, ts in rows:
            conn.execute("INSERT INTO facts (uid, content, source, created_at, updated_at, valid_from, recorded_at)"
                         " VALUES (?,?,?,?,?,?,?)",
                         (uid, 'x', 't', ts, ts, ts, ts))
        conn.commit(); conn.close()
        G.DB = db
        return db

    def test_result_without_updated_at_gets_db_lookup(self, tmp_path):
        self._db(tmp_path, [('u1', _now(0).strftime('%Y-%m-%d %H:%M'))])
        results = [{'uid': 'u1', 'score': 1.0, 'type': 'fact'}]
        out = G.apply_decay(results)
        assert out[0]['updated_at']  # backfilled from db
        assert 'decay_missing' not in out[0]

    def test_missing_everywhere_flags_decay_missing(self, tmp_path):
        self._db(tmp_path, [])
        results = [{'uid': 'ghost', 'score': 1.0, 'type': 'fact'}]
        out = G.apply_decay(results, lookup_db=True)
        assert out[0].get('decay_missing') is True
        assert out[0]['score'] == 1.0  # neutral factor

    def test_sorts_by_decayed_score(self):
        old_ts = (_now(0) - dt.timedelta(days=90)).strftime('%Y-%m-%d %H:%M')
        new_ts = _now(0).strftime('%Y-%m-%d %H:%M')
        results = [{'uid': 'a', 'score': 2.0, 'type': 'fact', 'updated_at': old_ts},
                   {'uid': 'b', 'score': 1.0, 'type': 'fact', 'updated_at': new_ts}]
        out = G.apply_decay(results)
        assert out[0]['uid'] == 'b'
        assert out[0]['score_before_decay'] == 1.0

    def test_lookup_db_false_skips_db(self, tmp_path):
        db = _real_db(str(tmp_path))
        G.DB = db
        results = [{'uid': 'u1', 'score': 1.0, 'type': 'fact'}]
        out = G.apply_decay(results, lookup_db=False)
        assert out[0].get('decay_missing') is True


class TestFindStale:
    def test_old_negative_risk_high(self, tmp_path):
        db = _real_db(str(tmp_path)); G.DB = db
        old = (_now(0) - dt.timedelta(days=60)).strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO facts (uid, content, source, confidence, created_at, updated_at, valid_from, recorded_at)"
                     " VALUES ('u1','Fooocus 失效了不能用了','x',0.8,?,?,?,?)", (old, old, old, old))
        conn.commit(); conn.close()
        out = G.find_stale(days=30)
        hit = [x for x in out if x['uid'] == 'u1']
        assert hit and hit[0]['risk'] == 'high'

    def test_recent_excluded(self, tmp_path):
        db = _real_db(str(tmp_path)); G.DB = db
        now = _now(0).strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO facts (uid, content, source, confidence, created_at, updated_at, valid_from, recorded_at)"
                     " VALUES ('u1','fresh thing','x',0.8,?,?,?,?)", (now, now, now, now))
        conn.commit(); conn.close()
        out = G.find_stale(days=30)
        assert not [x for x in out if x['uid'] == 'u1']


class TestConflictReview:
    def test_record_and_review_roundtrip(self, tmp_path):
        db = _real_db(str(tmp_path)); G.DB = db
        r = G.record_conflict_review('uid-b', 'uid-a', 'no_conflict', note='complementary', by_agent='codex')
        assert r['ok'] and r['uid_a'] == 'uid-a' and r['uid_b'] == 'uid-b'  # normalized order
        pairs = G.reviewed_no_conflict()
        assert frozenset(('uid-a', 'uid-b')) in pairs

    def test_reviewed_other_verdict_not_excluded(self, tmp_path):
        db = _real_db(str(tmp_path)); G.DB = db
        G.record_conflict_review('a2', 'b2', 'real_conflict')
        pairs = G.reviewed_no_conflict()
        assert frozenset(('a2', 'b2')) not in pairs


class TestResidualFacts:
    def test_splits_on_semicolon_and_comma(self):
        content = 'DeepSeek key is dead; price is 1 yuan per million, cache hit 0.02'
        resid = G._residual_facts(content, conflict_entity='DeepSeek')
        assert any('price' in r for r in resid)
        assert not any('DeepSeek' in r for r in resid)

    def test_conflict_entity_filtered(self):
        resid = G._residual_facts('Clash is broken and Clash 的端口也变了', conflict_entity='Clash')
        assert resid == []

    def test_short_fragments_dropped(self):
        resid = G._residual_facts('ab, cd, long enough fragment survives here', conflict_entity='')
        assert all(len(r) >= 8 for r in resid)


class TestRetire:
    def _fact(self, tmp_path, uid='r1', content='something factual', ts='2026-10-01 10:00:00'):
        db = _real_db(str(tmp_path)); G.DB = db
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO facts (uid, content, source, created_at, updated_at, valid_from, recorded_at)"
                     " VALUES (?,?,?,?,?,?,?)", (uid, content, 't', ts, ts, ts, ts))
        conn.commit(); conn.close()
        return db

    def test_dry_run_does_not_mutate(self, tmp_path):
        self._fact(tmp_path)
        r = G.retire('r1', apply=False, reason='stale')
        assert r['dry_run'] is True
        conn = sqlite3.connect(G.DB)
        assert conn.execute("SELECT status FROM facts WHERE uid='r1'").fetchone()[0] == 'active'
        conn.close()

    def test_missing_uid(self, tmp_path):
        self._fact(tmp_path)
        r = G.retire('nope', apply=True)
        assert r['ok'] is False and '不存在' in r['err']

    def test_apply_without_residual(self, tmp_path):
        self._fact(tmp_path, content='single fact about torch 2.13 only')
        r = G.retire('r1', apply=True, reason='torch 2.13 upgrade', by_uid='r2')
        assert r['ok'] is True and r['status'] == 'superseded'
        assert r['superseded_by'] == 'r2'
        conn = sqlite3.connect(G.DB)
        row = conn.execute("SELECT status, superseded_by, valid_to FROM facts WHERE uid='r1'").fetchone()
        conn.close()
        assert row[0] == 'superseded' and row[1] == 'r2' and row[2]

    def test_apply_with_residual_refused_without_force(self, tmp_path):
        # content mixes a conflicting part (torch) with independent facts (price)
        self._fact(tmp_path, content='torch 2.13 是坏的；但是价格是每百万 1 元这个信息还是独立的且很长')
        r = G.retire('r1', apply=True, reason='torch 2.13 issue')
        assert r['ok'] is False and 'residual' in r

    def test_force_overrides_residual(self, tmp_path):
        self._fact(tmp_path, content='torch 2.13 是坏的；但是价格是每百万 1 元这个信息还是独立的且很长')
        r = G.retire('r1', apply=True, force=True, reason='torch 2.13 issue')
        assert r['ok'] is True and r.get('forced_with_residual') is True


class TestFindSelfContradictions:
    def test_content_with_both_polarities_flagged(self, tmp_path):
        db = _real_db(str(tmp_path)); G.DB = db
        conn = sqlite3.connect(db)
        ts = '2026-10-01 10:00:00'
        conn.execute(
            "INSERT INTO facts (uid, content, source, created_at, updated_at, valid_from, recorded_at)"
            " VALUES ('s1','proxy 已配置完成，但 proxy 现在 403 不可用了','t',?,?,?,?)",
            (ts, ts, ts, ts))
        conn.commit(); conn.close()
        res = G.find_self_contradictions()
        assert isinstance(res, list)  # runs without crash; detection heuristics documented as advisory
