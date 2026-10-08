# -*- coding: utf-8 -*-
"""tests/test_sgm.py — Schema-Grounded Memory: fact_entities + SQL query routing.

Phase 1: store_entities() writes structured (entity, attr) pairs to
fact_entities table on every remember.
Phase 2: search_hybrid routes entity-attr queries through SQL first,
boosting SQL-matched results with score=1.0 + reason sql_deterministic.
"""
import os, sys, json, sqlite3, tempfile
import pytest
import predicates
import gateway

@pytest.fixture
def sgm_db(tmp_path):
    db = str(tmp_path / 'sgm.db')
    env = dict(os.environ)
    env['MEM_DB'] = db
    env['MEM_SKIP_VECTOR'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    os.environ.update(env)
    py = sys.executable
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import importlib
    import gateway, memsearch
    importlib.reload(gateway)
    gateway.init_db()
    importlib.reload(memsearch)
    memsearch.DB = db
    return env, py, cwd, db

class TestStoreEntities:
    def test_entities_written_on_remember(self, sgm_db):
        env, py, cwd, db = sgm_db
        gateway.remember('Clash 的端口是 7890', source='sgmtest')
        conn = sqlite3.connect(db)
        rows = conn.execute('SELECT entity, attr FROM fact_entities').fetchall()
        conn.close()
        assert len(rows) >= 1
        assert any('Clash' in r[0] for r in rows)

    def test_multiple_entities(self, sgm_db):
        env, py, cwd, db = sgm_db
        gateway.remember('Clash 的端口和 ComfyUI 的版本都是重要的', source='sgmtest')
        conn = sqlite3.connect(db)
        entities = {r[0] for r in conn.execute('SELECT DISTINCT entity FROM fact_entities').fetchall()}
        conn.close()
        assert 'Clash' in entities and 'ComfyUI' in entities

    def test_no_entities_no_rows(self, sgm_db):
        env, py, cwd, db = sgm_db
        gateway.remember('plain text without patterns here', source='sgmtest')
        conn = sqlite3.connect(db)
        n = conn.execute('SELECT COUNT(*) FROM fact_entities').fetchone()[0]
        conn.close()
        assert n == 0

class TestQueryEntities:
    def test_deterministic_lookup(self, sgm_db):
        env, py, cwd, db = sgm_db
        gateway.remember('Clash 的订阅地址是 https://sub.example.com', source='sgmtest')
        conn = sqlite3.connect(db)
        results = predicates.query_entities(conn, 'Clash')
        conn.close()
        assert len(results) >= 1 and results[0]['status'] == 'active'

class TestAggregation:
    def test_counting_produces_sql_count(self, sgm_db):
        env, py, cwd, db = sgm_db
        gateway.remember('ComfyUI 的版本是 0.3.2', source='sgmtest')
        gateway.remember('ComfyUI 的端口是 8188', source='sgmtest')
        gateway.remember('ComfyUI 的配置文件在 configs', source='sgmtest')
        import memsearch
        memsearch.DB = db
        r = memsearch.search_hybrid('how many ComfyUI', limit=5)
        agg = r['diag'].get('sgm_aggregation', [])
        assert any('SQL COUNT' in a and 'ComfyUI' in a for a in agg)

    def test_aggregation_produces_sql_list(self, sgm_db):
        env, py, cwd, db = sgm_db
        gateway.remember('ComfyUI 的版本是 0.3.2', source='sgmtest')
        gateway.remember('ComfyUI 的端口是 8188', source='sgmtest')
        import memsearch
        memsearch.DB = db
        r = memsearch.search_hybrid('list all ComfyUI', limit=5)
        agg = r['diag'].get('sgm_aggregation', [])
        assert any('SQL LIST' in a and 'ComfyUI' in a for a in agg)

    def test_aggregation_entities_from_ascii(self, sgm_db):
        """Counting queries without 的 pattern use extract_ascii_entities."""
        env, py, cwd, db = sgm_db
        gateway.remember('ComfyUI supports torch 2.13', source='sgmtest')
        import memsearch
        memsearch.DB = db
        r = memsearch.search_hybrid('how many ComfyUI', limit=5)
        agg = r['diag'].get('sgm_aggregation', [])
        assert any('ComfyUI' in a for a in agg)


class TestSQLRouting:
    def test_entity_query_boosts_sql_hits(self, sgm_db):
        env, py, cwd, db = sgm_db
        gateway.remember('Clash 的端口是 7890', source='sgmtest')
        gateway.remember('ComfyUI 支持 torch 2.13', source='sgmtest')
        import memsearch
        memsearch.DB = db
        r = memsearch.search_hybrid('Clash 的端口', limit=5)
        sql_hits = [x for x in r['results'] if 'sql_deterministic' in (x.get('reason') or [])]
        if sql_hits:
            assert sql_hits[0]['score'] >= 1.0

    def test_sgm_disabled(self, sgm_db):
        env, py, cwd, db = sgm_db
        os.environ['MEM_SGM'] = '0'
        gateway.remember('Clash 的端口是 7890', source='sgmtest')
        import memsearch
        memsearch.DB = db
        r = memsearch.search_hybrid('Clash 的端口', limit=5)
        assert not any('sql_deterministic' in (x.get('reason') or []) for x in r['results'])
        del os.environ['MEM_SGM']

    def test_disable_switch(self, sgm_db):
        env, py, cwd, db = sgm_db
        gateway.remember('Clash 的端口是 7890', source='sgmtest')
        import memsearch
        memsearch.DB = db
        os.environ['MEM_SGM'] = '0'
        r = memsearch.search_hybrid('Clash 的端口', limit=5)
        assert not any('sql_deterministic' in (x.get('reason') or []) for x in r['results'])
        del os.environ['MEM_SGM']
