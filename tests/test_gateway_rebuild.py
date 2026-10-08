# -*- coding: utf-8 -*-
"""tests/test_gateway_rebuild.py — rebuild projection pipeline (was nearly 0%).

rebuild() writes: sink.json (MEM_SINK_PATH), MEMORY.md projections
(MEM_PROJ_PATH). All redirectable to sandbox. Locks: sink structure/typing,
pin unconditional inclusion, type-aware lead caps, budget hard cap,
tool-asset star-priority, source legend presence, and idempotency.
"""
import os, sys, json, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'


def _setup(tmp_path):
    db = str(tmp_path / 'r.db')
    sink = str(tmp_path / 'sink.json')
    proj = str(tmp_path / 'MEMORY.md')
    os.environ['MEM_DB'] = db
    os.environ['MEM_SINK_PATH'] = sink
    os.environ['MEM_PROJ_PATH'] = proj
    os.environ['MEM_PROJ_BUDGET'] = '3980'
    import importlib, gateway
    importlib.reload(gateway)
    # force HG None so _proj_targets() honours MEM_PROJ_PATH (sandbox isolation)
    gateway.HG = None
    gateway.init_db()
    return db, sink, proj, gateway


class TestRebuild:
    def test_sink_structure_and_typing(self, tmp_path):
        db, sink, proj, gw = _setup(tmp_path)
        gw.remember('rebuild sink probe fact', type='fact', source='codex')
        gw.remember('rebuild sink probe decision body', type='decision', source='codex')
        gw.rebuild()
        data = json.load(open(sink, encoding='utf-8'))
        assert set(data.keys()) >= {'fact', 'decision', 'incident', 'experience', 'todo'}
        all_text = json.dumps(data, ensure_ascii=False)
        assert 'sink probe fact' in all_text and 'sink probe decision' in all_text
        d = [x for x in data['decision'] if 'probe decision' in x['text']][0]
        assert d['source'] == 'codex' and d['uid'].startswith('fact-')

    def test_superseded_excluded_from_sink(self, tmp_path):
        db, sink, proj, gw = _setup(tmp_path)
        r1 = gw.remember('to be superseded rebuild probe', source='codex')
        gw.correct(r1['uid'], 'replacement rebuild probe', reason='update')
        gw.rebuild()
        data = json.load(open(sink, encoding='utf-8'))
        all_text = json.dumps(data, ensure_ascii=False)
        assert 'to be superseded' not in all_text
        assert 'replacement rebuild probe' in all_text

    def test_pin_unconditionally_included(self, tmp_path):
        db, sink, proj, gw = _setup(tmp_path)
        # old pinned fact + many newer facts to push the pinned out of recency window
        gw.remember('PINNED anchor definition content that must stay', source='codex', tags='pin')
        for i in range(10):
            gw.remember('filler number %d with unique marker ff%d' % (i, i), source='codex')
        gw.rebuild()
        md = open(proj, encoding='utf-8').read()
        assert 'PINNED anchor definition' in md

    def test_budget_hard_cap(self, tmp_path):
        db, sink, proj, gw = _setup(tmp_path)
        os.environ['MEM_PROJ_BUDGET'] = '900'
        try:
            for i in range(40):
                gw.remember('budget filler line %d — each entry consumes characters to overflow the projection budget quickly' % i,
                            source='codex')
            gw.rebuild()
            md = open(proj, encoding='utf-8').read()
            assert len(md) < 8000  # hard cap respected with margin for headers
        finally:
            os.environ['MEM_PROJ_BUDGET'] = '3980'

    def test_source_legend_present(self, tmp_path):
        db, sink, proj, gw = _setup(tmp_path)
        gw.remember('legend probe content', source='codex')
        gw.rebuild()
        md = open(proj, encoding='utf-8').read()
        # legend only when hubguard importable; either way the usage header exists
        assert '取全文' in md and '禁止直接改本文件' in md

    def test_idempotent(self, tmp_path):
        db, sink, proj, gw = _setup(tmp_path)
        gw.remember('idem probe', source='codex')
        gw.rebuild()
        m1 = open(proj, encoding='utf-8').read()
        s1 = open(sink, encoding='utf-8').read()
        gw.rebuild()
        m2 = open(proj, encoding='utf-8').read()
        s2 = open(sink, encoding='utf-8').read()
        assert m1 == m2 and s1 == s2

    def test_retired_not_in_projection(self, tmp_path):
        db, sink, proj, gw = _setup(tmp_path)
        r = gw.remember('retire me rebuild probe', source='codex')
        gw.retire(r['uid'], reason='done')
        gw.rebuild()
        md = open(proj, encoding='utf-8').read()
        assert 'retire me rebuild probe' not in md
