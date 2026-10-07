# -*- coding: utf-8 -*-
"""tests/test_predicates.py — predicates.py coverage (was 28%, import-only via gateway).

gateway.py:597 calls store_predicate on every remember; check_attr_coverage is
the retrieval-side validator (P2-2 marked NOT wired into memsearch yet).
These tests lock extraction patterns + storage + scoring behavior.
"""
import os, sys, json, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'

import predicates as P


class TestExtractPredicates:
    def test_attr_pattern_chinese(self):
        preds = P.extract_predicates('Clash 的订阅地址是 https://x.example')
        pairs = {(p['e'], p['a']) for p in preds}
        assert any(e == 'Clash' and a.startswith('订阅地址') for e, a in pairs)

    def test_support_pattern(self):
        preds = P.extract_predicates('ComfyUI 支持 torch 2.13')
        pairs = {(p['e'], p['a']) for p in preds}
        assert ('ComfyUI', 'torch') in pairs

    def test_bracket_prefix_stripped(self):
        preds = P.extract_predicates('【fact】Clash 的端口是 7890')
        assert any(p['e'] == 'Clash' for p in preds)

    def test_pronoun_entities_filtered(self):
        preds = P.extract_predicates('我的名字是 X，它的端口是 Y')
        assert all(p['e'] not in ('我', '它') for p in preds)

    def test_empty_content(self):
        assert P.extract_predicates('') == []
        assert P.extract_predicates(None) == []

    def test_max_eight(self):
        content = ' '.join(f'实体{i} 的属性{i}是值{i}' for i in range(15))
        assert len(P.extract_predicates(content)) <= 8


class TestStorePredicate:
    def _db(self, tmpdir):
        db = os.path.join(str(tmpdir), 'p.db')
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE facts (uid TEXT PRIMARY KEY, content TEXT, predicate TEXT)")
        conn.execute("INSERT INTO facts (uid, content) VALUES ('u1', 'Clash 的订阅地址已更新')")
        conn.commit()
        return db, conn

    def test_store_writes_json(self, tmp_path):
        db, conn = self._db(tmp_path)
        preds = P.store_predicate(conn, 'u1', 'Clash 的订阅地址已更新')
        assert preds
        row = conn.execute("SELECT predicate FROM facts WHERE uid='u1'").fetchone()
        stored = json.loads(row[0])
        assert any(p['e'] == 'Clash' for p in stored)
        conn.close()

    def test_store_no_match_leaves_null(self, tmp_path):
        db, conn = self._db(tmp_path)
        preds = P.store_predicate(conn, 'u1', 'plain statement without patterns')
        assert preds == []
        row = conn.execute("SELECT predicate FROM facts WHERE uid='u1'").fetchone()
        assert row[0] is None
        conn.close()


class TestCheckAttrCoverage:
    def _results(self, *contents):
        return [{'content': c, 'score': 1.0} for c in contents]

    def test_non_attr_query_passthrough(self):
        results = self._results('something about clash')
        out, all_un = P.check_attr_coverage('clash', results)
        assert all_un is False
        assert out[0]['score'] == 1.0

    def test_entity_without_attr_downweighted(self):
        results = self._results('Clash 端口配置说明')  # mentions entity, lacks attr? attr=订阅
        out, all_un = P.check_attr_coverage('Clash 的订阅', results)
        # content mentions Clash but not 订阅 → downweighted
        assert out[0].get('not_answered') is True
        assert out[0]['score'] == 0.15

    def test_entity_with_attr_answered(self):
        results = self._results('Clash 的订阅地址是 https://x')
        out, all_un = P.check_attr_coverage('Clash 的订阅', results)
        assert out[0].get('not_answered') is None
        assert all_un is False

    def test_entity_absent_neutral(self):
        results = self._results('unrelated memory content')
        out, all_un = P.check_attr_coverage('Clash 的订阅', results)
        assert out[0]['score'] == 1.0
        assert 'not_answered' not in out[0]
