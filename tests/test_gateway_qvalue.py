# -*- coding: utf-8 -*-
"""tests/test_gateway_qvalue.py — bump_qvalue full contract (was 0% direct).

Q-Value is a headline feature (README claim). Locks: distribution readonly
mode, reward validation, convergence formula, dry-run (apply=False),
tool_assets fallback, ghost uid error, and audit trail.
"""
import os, sys, json, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'


def _setup(tmp_path):
    db = str(tmp_path / 'q.db')
    os.environ['MEM_DB'] = db
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()
    return db, gateway


class TestQvalueContract:
    def test_distribution_readonly_mode(self, tmp_path):
        db, gw = _setup(tmp_path)
        gw.remember('dist probe a', source='codex')
        out = gw.bump_qvalue()  # no uid → distribution
        assert out['ok'] is True and out['readonly'] is True
        assert out['active'] >= 1
        assert out['init'] == 0.5 and out['lr'] > 0
        assert isinstance(out['top'], list)

    def test_reward_validation(self, tmp_path):
        db, gw = _setup(tmp_path)
        assert gw.bump_qvalue(uid='x', reward='abc')['ok'] is False
        assert gw.bump_qvalue(uid='x', reward=1.5)['ok'] is False
        assert gw.bump_qvalue(uid='x', reward=-0.1)['ok'] is False

    def test_convergence_towards_reward(self, tmp_path):
        db, gw = _setup(tmp_path)
        uid = gw.remember('conv probe', source='codex')['uid']
        # full reward repeatedly → q converges towards 1.0
        last = None
        for _ in range(5):
            last = gw.bump_qvalue(uid=uid, reward=1.0, agent='codex')
        assert last['q_value_after'] > last['q_value_before']
        assert last['q_value_after'] <= 1.0
        # zero reward repeatedly → converges towards 0.0 but floor via formula only
        uid2 = gw.remember('sink probe', source='codex')['uid']
        last2 = gw.bump_qvalue(uid=uid2, reward=0.0, agent='codex')
        assert last2['q_value_after'] < last2['q_value_before']
        assert last2['score_factor'] >= 0.3  # documented floor for score factor

    def test_use_count_increments(self, tmp_path):
        db, gw = _setup(tmp_path)
        uid = gw.remember('usecount probe', source='codex')['uid']
        out = gw.bump_qvalue(uid=uid, reward=1.0, agent='codex')
        assert out['use_count_after'] == out['use_count_before'] + 1

    def test_dry_run_no_write(self, tmp_path):
        db, gw = _setup(tmp_path)
        uid = gw.remember('dryrun probe', source='codex')['uid']
        out = gw.bump_qvalue(uid=uid, reward=1.0, agent='codex', apply=False)
        assert out['applied'] is False
        conn = sqlite3.connect(db)
        q, n = conn.execute("SELECT q_value, use_count FROM facts WHERE uid=?", (uid,)).fetchone()
        conn.close()
        assert q == 0.5 and n == 0  # untouched

    def test_audit_trail_written(self, tmp_path):
        db, gw = _setup(tmp_path)
        uid = gw.remember('audit probe', source='codex')['uid']
        gw.bump_qvalue(uid=uid, reward=1.0, agent='codex', detail='test bump')
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE op='qvalue'").fetchone()[0]
        conn.close()
        assert n >= 1

    def test_ghost_uid_error(self, tmp_path):
        db, gw = _setup(tmp_path)
        out = gw.bump_qvalue(uid='fact-ghost-xyz', reward=1.0, agent='codex')
        assert out['ok'] is False and '不存在' in out['error']

    def test_tool_assets_fallback(self, tmp_path):
        db, gw = _setup(tmp_path)
        gw.record_tool('probe_tool_qv', path='/tmp/x', source='codex')
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT uid FROM tool_assets WHERE name='probe_tool_qv'").fetchone()
        conn.close()
        out = gw.bump_qvalue(uid=row[0], reward=1.0, agent='codex')
        assert out['ok'] is True and out['table'] == 'tool_assets'


class TestRecordToolAndIncident:
    def test_record_tool_roundtrip(self, tmp_path):
        db, gw = _setup(tmp_path)
        gw.record_tool('rt_probe', path='/tmp/rt', aliases='alias1,alias2',
                       capabilities='cap1', source='codex')
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT name, aliases, path FROM tool_assets WHERE name='rt_probe'").fetchone()
        conn.close()
        assert row and row[0] == 'rt_probe' and 'alias1' in row[1]

    def test_incident_write(self, tmp_path):
        db, gw = _setup(tmp_path)
        r = gw.incident('step1', 'boom error', workaround='avoid it', agent='codex')
        # real contract: incident lands in run_events (reflector queue), not facts
        assert r.get('ok') is True
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM run_events WHERE event_type='incident'").fetchone()[0]
        conn.close()
        assert n >= 1

    def test_on_miss_no_answer(self, tmp_path):
        db, gw = _setup(tmp_path)
        out = gw.on_miss('unanswerable query xyz', answer_source='none')
        assert out is not None  # runs without crash
