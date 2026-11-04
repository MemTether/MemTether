# -*- coding: utf-8 -*-
"""Regression tests: rebuild selection sync (procedure quota + today rescue)."""
import os, sys, json, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'


def _setup(tmp_path, budget='3980'):
    db = str(tmp_path / 'sync.db')
    proj = str(tmp_path / 'MEMORY.md')
    os.environ['MEM_DB'] = db
    os.environ['MEM_SINK_PATH'] = str(tmp_path / 'sink.json')
    os.environ['MEM_PROJ_PATH'] = proj
    os.environ['MEM_PROJ_BUDGET'] = budget
    import importlib, gateway
    importlib.reload(gateway)
    gateway.HG = None
    gateway.init_db()
    return gateway, proj


def test_procedure_has_quota_base(tmp_path):
    """procedure entries must be a first-class quota type, not a fallback."""
    gw, proj = _setup(tmp_path)
    for i in range(3):
        gw.remember('procedure step doc %d: do X then Y carefully' % i,
                    type='procedure', source='codex')
    gw.rebuild()
    md = open(proj, encoding='utf-8').read()
    assert 'procedure step doc 0' in md


def test_today_rescue_protects_same_day_facts(tmp_path):
    """A fact written today must appear in the projection even under pressure."""
    gw, proj = _setup(tmp_path, budget='700')
    gw.remember('TODAY MARKER fact that must survive budget pressure', source='codex')
    # flood with older-looking rows cannot exist (all written now); instead flood volume
    for i in range(30):
        gw.remember('pressure filler %d padding padding padding padding padding' % i, source='codex')
    gw.rebuild()
    md = open(proj, encoding='utf-8').read()
    assert 'TODAY MARKER' in md, 'same-day fact was squeezed out - today rescue missing'
