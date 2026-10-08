# -*- coding: utf-8 -*-
"""tests/test_governance_detect.py — detect_explicit_conflicts + _canonical coverage.

These two paths push governance.py past 50%: _canonical entity normalization
(alias table + suffix stripping) and detect_explicit_conflicts cross-entity
polarity logic (the "low-FP automated detection" headline feature).
"""
import os, sys, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'

import governance as G


def _db(tmpdir):
    db = os.path.join(str(tmpdir), 'gd.db')
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE facts (
        uid TEXT PRIMARY KEY, type TEXT, subject TEXT, content TEXT,
        status TEXT DEFAULT 'active', superseded_by TEXT,
        valid_from TEXT, valid_to TEXT, source TEXT, scope TEXT DEFAULT 'shared',
        confidence REAL DEFAULT 1.0, tags TEXT, created_at TEXT, updated_at TEXT,
        recorded_at TEXT, invalidated_at TEXT, temporal_source TEXT,
        q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0)""")
    conn.commit(); conn.close()
    return db


class TestCanonical:
    def test_alias_normalization(self):
        assert G._canonical('GPTX_ASTRA_KEY') == G._canonical('gptx_astra')

    def test_suffix_stripping(self):
        # some_key → base via suffix rule; both map identically
        assert G._canonical('SILICONFLOW_KEY') == G._canonical('SILICONFLOW')

    def test_whitespace_and_brackets_removed(self):
        assert G._canonical('《 DeepSeek 官方 》') == G._canonical('DeepSeek官方')

    def test_stopword_not_special(self):
        out = G._canonical('the')
        assert isinstance(out, str)


class TestDetectExplicitConflicts:
    def _facts(self, tmp_path, rows):
        db = _db(str(tmp_path)); G.DB = db
        conn = sqlite3.connect(db)
        for uid, content, ts in rows:
            conn.execute("INSERT INTO facts (uid, content, source, created_at, updated_at, valid_from, recorded_at)"
                         " VALUES (?,?,?,?,?,?,?)", (uid, content, 't', ts, ts, ts, ts))
        conn.commit(); conn.close()
        return db

    def test_cross_entity_conflict_detected(self, tmp_path):
        self._facts(tmp_path, [
            ('a1', 'OPENAI_API_KEY 已充值可用', '2026-10-01 10:00'),
            ('a2', 'OPENAI_API_KEY 401 失效了', '2026-10-02 10:00'),
        ])
        res = G.detect_explicit_conflicts(days_window=90)
        hits = [r for r in res if r['entity'] and 'openai' in r['entity'].lower()]
        assert hits, res
        h = hits[0]
        assert h['newer'] == 'neg'
        assert h['suggest_retire'] == 'a1'

    def test_newer_positive_suggests_retiring_old_negative(self, tmp_path):
        self._facts(tmp_path, [
            ('b1', 'SILICONFLOW 403 不可用', '2026-10-01 10:00'),
            ('b2', 'SILICONFLOW 已充值可用', '2026-10-02 10:00'),
        ])
        res = G.detect_explicit_conflicts(days_window=90)
        hits = [r for r in res if 'siliconflow' in r['entity'].lower()]
        assert hits
        assert hits[0]['newer'] == 'pos'
        assert hits[0]['suggest_retire'] == 'b1'

    def test_same_entity_single_polarity_no_report(self, tmp_path):
        self._facts(tmp_path, [
            ('c1', 'OPENAI_API_KEY 可用正常', '2026-10-01 10:00'),
            ('c2', 'OPENAI_API_KEY 连通正常恢复', '2026-10-02 10:00'),
        ])
        res = G.detect_explicit_conflicts(days_window=90)
        assert not [r for r in res if 'openai' in r['entity'].lower()]

    def test_stale_rows_outside_window_excluded(self, tmp_path):
        self._facts(tmp_path, [
            ('d1', 'OPENAI_API_KEY 已充值可用', '2026-01-01 10:00'),
            ('d2', 'OPENAI_API_KEY 401 失效', '2026-10-02 10:00'),
        ])
        res = G.detect_explicit_conflicts(days_window=30)
        assert not [r for r in res if 'openai' in r['entity'].lower()]
