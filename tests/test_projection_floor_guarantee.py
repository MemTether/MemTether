# -*- coding: utf-8 -*-
"""Regression tests: floor-first anti-starvation + pin/floor lead caps."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'


def _setup(tmp_path, budget='3980'):
    db = str(tmp_path / 'starve.db')
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


def test_floor_survives_when_pins_are_heavy(tmp_path):
    """10 heavy pins must not starve floor types (prod regression 2026-11-04)."""
    gw, proj = _setup(tmp_path, budget='1600')
    # 10 pins with long content (the prod failure shape)
    for i in range(10):
        gw.remember('pin definition %d: ' % i + ('很长的定义内容。' * 25), source='codex', tags='pin')
    # every type present with short floors
    gw.remember('short incident a', type='incident', source='codex')
    gw.remember('short incident b', type='incident', source='codex')
    gw.remember('short procedure a', type='procedure', source='codex')
    gw.remember('short procedure b', type='procedure', source='codex')
    gw.rebuild()
    md = open(proj, encoding='utf-8').read()
    assert 'short incident a' in md, 'incident starved by pins'
    assert 'short procedure a' in md, 'procedure starved by pins'


def test_pin_lead_capped(tmp_path):
    """A pin whose lead exceeds the cap must be truncated, not budget-breaking."""
    gw, proj = _setup(tmp_path)
    gw.remember('pin huge: ' + ('超长内容' * 200), source='codex', tags='pin')
    gw.rebuild()
    md = open(proj, encoding='utf-8').read()
    line = [l for l in md.splitlines() if 'pin huge' in l][0]
    assert len(line) < 200, 'pin lead not capped: %d' % len(line)


def test_pin_lead_capped(tmp_path):
    """A pin whose lead exceeds the cap must be truncated, not budget-breaking."""
    gw, proj = _setup(tmp_path)
    gw.remember('pin huge: ' + ('超长内容' * 200), source='codex', tags='pin')
    gw.rebuild()
    md = open(proj, encoding='utf-8').read()
    line = [l for l in md.splitlines() if 'pin huge' in l][0]
    assert len(line) < 200, 'pin lead not capped: %d' % len(line)
