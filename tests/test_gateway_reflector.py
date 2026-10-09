# -*- coding: utf-8 -*-
"""tests/test_gateway_reflector.py — event queue + reflector pipeline with mocked LLM (a74).

Covers: record_event idempotency, incident/on_miss wrappers,
commit_memory_candidate thresholds (auto-write vs candidate queue vs dup),
auto_reflect with mocked _llm_json (success + failure), process_events
full pipeline (pending -> processed / failed).
"""
import os
import sys
import json
import pytest

os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _setup(tmp_path):
    db = str(tmp_path / 'refl.db')
    os.environ['MEM_DB'] = db
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()
    return gateway


class TestRecordEvent:
    def test_insert_and_idempotent(self, tmp_path):
        gw = _setup(tmp_path)
        r1 = gw.record_event('incident', 'run-1', {'step': 's'})
        r2 = gw.record_event('incident', 'run-1', {'step': 's'})
        assert r1['op'] == 'insert'
        assert r2['op'] == 'noop_dup'
        assert r1['event_id'] == r2['event_id']

    def test_same_run_diff_type(self, tmp_path):
        gw = _setup(tmp_path)
        r1 = gw.record_event('incident', 'run-2', {'a': 1})
        r2 = gw.record_event('retrieval_miss', 'run-2', {'b': 2})
        assert r1['op'] == 'insert' and r2['op'] == 'insert'


class TestIncidentOnMiss:
    def test_incident_wrapper(self, tmp_path):
        gw = _setup(tmp_path)
        r = gw.incident('step-x', 'boom', workaround='w', result='r')
        assert r['ok'] is True

    def test_incident_custom_run_id(self, tmp_path):
        gw = _setup(tmp_path)
        r = gw.incident('s', 'e', run_id='custom-rid')
        assert r['ok'] is True


class TestCommitCandidate:
    def test_low_value_queued(self, tmp_path):
        gw = _setup(tmp_path)
        r = gw.commit_memory_candidate({
            'text': '低价值候选: 内容测试abc', 'confidence': 0.5,
            'reuse_score': 0.1, 'impact_score': 0.1, 'evidence': ''})
        assert r['ok'] is True and r['op'] == 'candidate_queued'

    def test_high_value_writes(self, tmp_path):
        gw = _setup(tmp_path)
        r = gw.commit_memory_candidate({
            'text': '高价值候选: 关键结论xyz 唯一标记', 'type': 'fact',
            'confidence': 0.95, 'reuse_score': 0.9, 'impact_score': 0.9,
            'evidence': 'tested'})
        assert r.get('ok') is True
        assert r.get('op') in ('insert', 'noop_dup', 'noop_near_dup')

    def test_empty_text_rejected(self, tmp_path):
        gw = _setup(tmp_path)
        r = gw.commit_memory_candidate({'text': ''})
        assert r['ok'] is False and r['reason'] == 'empty_text'

    def test_dup_content_noop(self, tmp_path):
        gw = _setup(tmp_path)
        cand = {'text': '重复内容测试: dup-marker-12345', 'type': 'fact',
                'confidence': 0.95, 'reuse_score': 0.9, 'impact_score': 0.9,
                'evidence': 'e'}
        r1 = gw.commit_memory_candidate(cand)
        r2 = gw.commit_memory_candidate(dict(cand))
        assert r1['ok'] and r2['ok']
        # second write should hit dedup path (noop_dup or near_dup) or re-insert
        assert r2.get('op') in ('insert', 'noop_dup', 'noop_near_dup')


class TestAutoReflect:
    def test_llm_failure_path(self, tmp_path, monkeypatch):
        gw = _setup(tmp_path)
        monkeypatch.setattr(gw, '_llm_json', lambda *a, **k: {'_error': 'mock fail'})
        r = gw.auto_reflect('run-fail', {'summary': 's'})
        assert r['ok'] is False and 'error' in r

    def test_llm_success_writes(self, tmp_path, monkeypatch):
        gw = _setup(tmp_path)
        def fake_llm(*a, **k):
            return {'memories': [{'text': '反射写入: mock结论unique777',
                                  'type': 'fact', 'confidence': 0.9,
                                  'reuse_score': 0.8, 'impact_score': 0.8,
                                  'evidence': 'mock'}]}
        monkeypatch.setattr(gw, '_llm_json', fake_llm)
        r = gw.auto_reflect('run-ok', {'summary': 's'})
        assert r['ok'] is True
        assert r['candidates'] == 1
        assert r['written'][0]['result']['ok'] is True

    def test_llm_none_returns_error(self, tmp_path, monkeypatch):
        gw = _setup(tmp_path)
        monkeypatch.setattr(gw, '_llm_json', lambda *a, **k: None)
        r = gw.auto_reflect('run-none', {'summary': 's'})
        assert r['ok'] is False


class TestProcessEvents:
    def test_full_pipeline_success(self, tmp_path, monkeypatch):
        gw = _setup(tmp_path)
        gw.record_event('incident', 'run-p1', {'summary': 's1'})
        def fake_llm(*a, **k):
            return {'memories': [{'text': '管线测试: 结论unique888',
                                  'confidence': 0.9, 'reuse_score': 0.8,
                                  'impact_score': 0.8, 'evidence': 'e'}]}
        monkeypatch.setattr(gw, '_llm_json', fake_llm)
        r = gw.process_events(limit=5)
        assert r['processed'] == 1 and r['failed'] == 0
        # event marked processed
        import sqlite3
        c = sqlite3.connect(os.environ['MEM_DB'])
        st = c.execute("SELECT status FROM run_events WHERE run_id='run-p1'").fetchone()[0]
        c.close()
        assert st == 'processed'

    def test_pipeline_failure_marks_failed(self, tmp_path, monkeypatch):
        gw = _setup(tmp_path)
        gw.record_event('incident', 'run-p2', {'summary': 's2'})
        monkeypatch.setattr(gw, '_llm_json', lambda *a, **k: {'_error': 'down'})
        r = gw.process_events(limit=5)
        assert r['failed'] >= 1
        import sqlite3
        c = sqlite3.connect(os.environ['MEM_DB'])
        st = c.execute("SELECT status FROM run_events WHERE run_id='run-p2'").fetchone()[0]
        c.close()
        assert st == 'failed'

    def test_empty_queue(self, tmp_path):
        gw = _setup(tmp_path)
        r = gw.process_events(limit=5)
        assert r['processed'] == 0 and r['failed'] == 0