# -*- coding: utf-8 -*-
"""tests/test_r6_integrity.py — fusion-review round 6 regression locks.

Three real defects found by code-level fusion review:
  1. correct() cycle → now refused
  2. correct() race → no fork (single supersessions edge)
  3. rebuild() sink leaked private facts + crashed without wb targets
"""
import os
import sys
import json
import sqlite3
import threading
import pytest

os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _setup(tmp_path, **env):
    db = str(tmp_path / 'r6.db')
    os.environ['MEM_DB'] = db
    for k, v in env.items():
        os.environ[k] = v
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()
    return gateway


class TestCycleRefusal:
    def test_cycle_chain_grows_without_error(self, tmp_path):
        gw = _setup(tmp_path)
        u1 = gw.remember('A: 8080', source='codex')['uid']
        u2 = gw.remember('B: 9090', source='codex')['uid']
        gw.correct(u1, 'A->B', reason='x', by_agent='codex')
        # correct on already-superseded uid must be refused (active guard)
        r = gw.correct(u1, 'A again', reason='y', by_agent='codex')
        assert r['ok'] is False
        assert 'not active' in r.get('error', '')


class TestRaceNoFork:
    def test_concurrent_correct_single_edge(self, tmp_path):
        gw = _setup(tmp_path)
        u = gw.remember('race original', source='codex')['uid']
        results = []
        def w(tag, val):
            r = gw.correct(u, val, reason=tag, by_agent='codex')
            results.append(r)
        threads = [threading.Thread(target=w, args=(f'T{i}', f'val{i}'))
                   for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        c = sqlite3.connect(os.environ['MEM_DB'])
        edges = c.execute(
            'SELECT COUNT(*) FROM supersessions WHERE old_uid=?',
            (u,)).fetchone()[0]
        c.close()
        assert edges == 1, f'FORK: {edges} supersessions edges from one uid'
        ok_count = sum(1 for r in results if r.get('ok'))
        assert ok_count == 1, 'exactly one correct must succeed'


class TestSinkScopePrivacy:
    def test_private_not_in_sink(self, tmp_path):
        sink = str(tmp_path / 'sink.json')
        gw = _setup(tmp_path, MEM_SINK_PATH=sink, MEM_PROJ_PATH=str(tmp_path / 'm.md'))
        gw.remember('public alpha', source='codex', scope='shared')
        gw.remember('secret KEY=xyz123', source='codex', scope='private')
        r = gw.rebuild()
        # open-source env: may skip wb targets
        data = json.loads(open(sink, encoding='utf-8').read())
        blob = json.dumps(data, ensure_ascii=False)
        assert 'xyz123' not in blob, 'PRIVATE fact leaked into sink.json'
        assert 'alpha' in blob, 'shared fact must be present'


class TestRebuildNoWbTargets:
    def test_rebuild_succeeds_without_wb(self, tmp_path):
        sink = str(tmp_path / 'sink.json')
        gw = _setup(tmp_path, MEM_SINK_PATH=sink, MEM_PROJ_PATH=str(tmp_path / 'm.md'))
        gw.remember('some fact', source='codex')
        r = gw.rebuild()
        assert r.get('ok') is True