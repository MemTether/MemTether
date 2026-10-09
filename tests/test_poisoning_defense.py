# -*- coding: utf-8 -*-
"""tests/test_poisoning_defense.py — memory-poisoning defense evidence (a74).

Maps PoisonRecall / Sleeper-memory-poisoning attack classes onto MemTether
defenses and PROVES each defense with an executable check:

  A. overwrite poisoning  -> supersession chain keeps old value, marks new
  B. erasure attack       -> retire() keeps the row (status change, not delete)
  C. untrusted-source write -> source registry rejects/demotes
  D. audit tamper         -> memory_court verify_chain catches edits
  E. sleeper retrieval    -> superseded entries excluded from default retrieval
"""
import os
import sys
import json
import sqlite3
import pytest

os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _setup(tmp_path):
    db = str(tmp_path / 'pd.db')
    os.environ['MEM_DB'] = db
    import importlib, gateway, memory_court
    importlib.reload(gateway)
    gateway.init_db()
    import memory_court as mc
    # memsearch resolves DB at import; without reload it points at a previous
    # test's tmp dir and search silently reads the wrong DB in later tests.
    try:
        import memsearch
        importlib.reload(memsearch)
    except Exception:
        pass
    return gateway, mc


def test_A_overwrite_poison_keeps_history(tmp_path):
    """Attacker writes a 'corrected' value to hijack behavior.
    Defense: supersession — old fact stays queryable, provenance recorded."""
    gw, _ = _setup(tmp_path)
    r1 = gw.remember('支付地址是 0xAAA111 (official)', source='codex')
    old_uid = r1['uid']
    r2 = gw.correct(old_uid, '支付地址是 0xEVIL222 (attacker)', reason='user msg',
                    by_agent='codex')
    assert r2['ok'] and r2['op'] == 'supersede'
    c = sqlite3.connect(os.environ['MEM_DB'])
    c.row_factory = sqlite3.Row
    old = c.execute('SELECT * FROM facts WHERE uid=?', (old_uid,)).fetchone()
    sup = c.execute('SELECT * FROM supersessions WHERE old_uid=?', (old_uid,)).fetchone()
    c.close()
    assert old is not None and old['status'] == 'superseded'
    assert old['content'] == '支付地址是 0xAAA111 (official)'  # history preserved
    assert sup is not None and sup['new_uid'] == r2['new_uid']


def test_B_erasure_attack_only_marks(tmp_path):
    """Attacker deletes via retire. Defense: retire() never deletes the row."""
    gw, _ = _setup(tmp_path)
    uid = gw.remember('审计线索: 合同编号 X-1', source='codex')['uid']
    gw.retire(uid, reason='attacker wipe', by_agent='codex')
    c = sqlite3.connect(os.environ['MEM_DB'])
    c.row_factory = sqlite3.Row
    row = c.execute('SELECT * FROM facts WHERE uid=?', (uid,)).fetchone()
    c.close()
    assert row is not None and row['status'] == 'retired'
    assert '合同编号 X-1' in row['content']


def test_C_unregistered_source_failopen_by_design(tmp_path, monkeypatch):
    """a74-r2 corrected per source: _guard_source is fail-open when agents.json
    is absent (open-source primary path — documented). So the honest assertions
    are: (1) open-source mode stores verbatim + audits; (2) WITH a registry
    that lacks the source, guard must NOT return the unknown name verbatim.
    """
    gw, _ = _setup(tmp_path)
    r = gw.remember('越权来源写入测试', source='ghost_agent_xyz')
    uid = r['uid']
    c = sqlite3.connect(os.environ['MEM_DB'])
    c.row_factory = sqlite3.Row
    row = c.execute('SELECT source FROM facts WHERE uid=?', (uid,)).fetchone()
    n = c.execute("SELECT COUNT(*) FROM audit_log WHERE target=?", (uid,)).fetchone()[0]
    c.close()
    # documented fail-open: verbatim storage in open-source mode
    assert row['source'] == 'ghost_agent_xyz'
    assert n >= 1, 'audit must record the write regardless of registry state'
    # registry mode: monkeypatch _known_sources to simulate deployed registry
    monkeypatch.setattr(gw, '_known_sources', lambda: {'codex', 'workbuddy'})
    with pytest.raises(SystemExit):
        gw._guard_source('ghost_agent_xyz')  # registry present -> hard reject

def test_D_audit_tamper_detected(tmp_path):
    """Direct DB edit of audit_log must break the hash chain."""
    gw, mc = _setup(tmp_path)
    for i in range(12):
        gw.remember(f'审计锚点测试条目 {i} 唯一标记 {i}', source='codex')
    ok, detail = mc.verify_chain(gw.get_conn())
    assert ok, f'chain must pass before tamper: {detail}'
    c = sqlite3.connect(os.environ['MEM_DB'])
    c.execute("UPDATE audit_log SET detail='tampered' WHERE id=(SELECT MIN(id) FROM audit_log)")
    c.commit()
    c.close()
    ok2, detail2 = mc.verify_chain(gw.get_conn())
    assert not ok2, 'chain must FAIL after direct edit'


def test_E_sleeper_superseded_not_served(tmp_path):
    """Sleeper pattern: poisoned fact later superseded -> default retrieval
    must not serve the superseded content as current truth."""
    gw, _ = _setup(tmp_path)
    gw.remember('配置端口为 1337（已过期结论）', source='codex')
    import sqlite3
    c = sqlite3.connect(os.environ['MEM_DB'])
    old = c.execute("SELECT uid FROM facts WHERE content LIKE '%1337%'").fetchone()[0]
    c.close()
    gw.correct(old, '配置端口为 8080（修正）', reason='port fix', by_agent='codex')
    r = gw.search('配置端口', limit=10)
    served = [x for x in r.get('results', []) if '1337' in (x.get('content') or '')]
    # a74-r2: non-tautology — every served row mentioning the stale value must
    # NOT be an active fact (it may only appear via supersession-chain notes).
    for x in served:
        assert x.get('status') != 'active' or 'superseded' in json.dumps(x.get('reason', '') + str(x.get('superseded_chain', '')), ensure_ascii=False), (
            f'stale value served as active fact: {x}')
    active = [x for x in r.get('results', []) if '8080' in (x.get('content') or '')]
    assert len(active) >= 1, 'corrected value must be retrievable'


def test_F_initial_injection_is_NOT_intercepted(tmp_path):
    """a74-r2 negative test — proves the HONEST LIMITATION: a fresh lie from any
    registered source becomes active and retrievable. Local-first does not do
    write-time truth verification; defense is post-hoc auditability.
    """
    gw, _ = _setup(tmp_path)
    r = gw.remember('攻击者新注入: 软件X官方下载地址是 evil.example.com', source='codex')
    uid = r['uid']
    res = gw.search('软件X 官方下载地址', limit=5)
    hit = [x for x in res.get('results', []) if uid == x.get('uid')]
    assert len(hit) == 1 and hit[0].get('content', '').startswith('攻击者新注入'), (
        'initial injection MUST be retrievable — if this fails, an unexpected'
        ' write-time filter exists and SECURITY.md limitation needs updating')


def test_G_cross_tenant_isolation(tmp_path):
    """a74-r2: facts in different tenants must not leak into each other's search.
    """
    gw, _ = _setup(tmp_path)
    os.environ['MEM_TENANT_ID'] = 'tenantA'
    gw.remember('租户A专属: 密钥是 AAA-111', source='codex')
    os.environ['MEM_TENANT_ID'] = 'tenantB'
    gw.remember('租户B专属: 密钥是 BBB-222', source='codex')
    resA = None
    # search as tenantA
    os.environ['MEM_TENANT_ID'] = 'tenantA'
    import importlib as _il, memsearch as _ms
    _il.reload(_ms)
    ra = _ms.search_hybrid('密钥', limit=10)
    texts_a = ' '.join((x.get('content') or '') for x in ra.get('results', []))
    assert 'BBB-222' not in texts_a, 'tenantB fact leaked into tenantA search'
    assert 'AAA-111' in texts_a, 'tenantA fact must be visible to tenantA'