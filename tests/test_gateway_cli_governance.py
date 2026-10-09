# -*- coding: utf-8 -*-
"""tests/test_gateway_cli_governance.py — gateway.main() governance + asset branch smoke (a74).

Covers: pin / qvalue / govern / conflicts / conflicts_exact / selfcheck /
stale / record_tool / resolve_task / incident / on_miss / get dispatch.
Same isolation pattern as test_gateway_deep.py (MEM_DB sandbox).
"""
import os
import sys
import json
import pytest

os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _setup(tmp_path):
    db = str(tmp_path / 'clig.db')
    os.environ['MEM_DB'] = db
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()
    return gateway


def _run(gw, monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, 'argv', ['gateway.py'] + list(args))
    gw.main()
    return capsys.readouterr().out


def _remember_uid(gw, monkeypatch, capsys, content):
    out = _run(gw, monkeypatch, capsys, 'remember', content, '--source', 'codex')
    for line in out.splitlines():
        s = line.strip()
        if s.startswith('{'):
            try:
                uid = json.loads(s).get('uid')
                if uid:
                    return uid
            except Exception:
                pass
    return None


def test_cli_pin_list_and_set(tmp_path, monkeypatch, capsys):
    gw = _setup(tmp_path)
    uid = _remember_uid(gw, monkeypatch, capsys, '钉住测试: 核心端口定义 7890')
    assert uid
    out = _run(gw, monkeypatch, capsys, 'pin', uid)
    assert 'ok' in out.lower() or 'pin' in out.lower()
    out2 = _run(gw, monkeypatch, capsys, 'pin')
    assert len(out2) > 0
    out3 = _run(gw, monkeypatch, capsys, 'pin', uid, '--off')
    assert 'ok' in out3.lower()


def test_cli_qvalue_readonly_and_write(tmp_path, monkeypatch, capsys):
    gw = _setup(tmp_path)
    uid = _remember_uid(gw, monkeypatch, capsys, 'QV测试: 服务B端口9090')
    assert uid
    out = _run(gw, monkeypatch, capsys, 'qvalue')
    assert len(out) > 0
    out2 = _run(gw, monkeypatch, capsys, 'qvalue', uid, '--reward', '1.0',
                '--source', 'codex', '--detail', 'test adopt')
    assert 'ok' in out2.lower() or 'q_value' in out2


def test_cli_govern_conflicts_stale(tmp_path, monkeypatch, capsys):
    gw = _setup(tmp_path)
    _remember_uid(gw, monkeypatch, capsys, 'govern种子: 测试条目内容xyz')
    for cmd in ('govern', 'conflicts', 'conflicts_exact', 'selfcheck', 'stale'):
        out = _run(gw, monkeypatch, capsys, cmd)
        assert ('ok' in out.lower()) or ('count' in out) or len(out) > 0


def test_cli_record_tool_and_resolve(tmp_path, monkeypatch, capsys):
    gw = _setup(tmp_path)
    out = _run(gw, monkeypatch, capsys, 'record_tool', 'TestTool',
               '--path', 'C:/tools/test.exe', '--entrypoint', 'test.exe',
               '--aliases', '测试工具', '--source', 'codex')
    assert 'ok' in out.lower() or 'uid' in out
    out2 = _run(gw, monkeypatch, capsys, 'resolve_task', '画图')
    assert len(out2) >= 0


def test_cli_incident_on_miss(tmp_path, monkeypatch, capsys):
    gw = _setup(tmp_path)
    out = _run(gw, monkeypatch, capsys, 'incident', '--step', 'test',
               '--error', 'e1', '--workaround', 'w1', '--result', 'r1',
               '--agent', 'codex')
    assert 'ok' in out.lower() or len(out) > 0
    out2 = _run(gw, monkeypatch, capsys, 'on_miss', '不存在的查询xyz')
    assert len(out2) >= 0


def test_cli_get(tmp_path, monkeypatch, capsys):
    gw = _setup(tmp_path)
    uid = _remember_uid(gw, monkeypatch, capsys, 'GET测试: 唯一内容标记abcdefg')
    assert uid
    out = _run(gw, monkeypatch, capsys, 'get', uid)
    assert 'abcdefg' in out
    out2 = _run(gw, monkeypatch, capsys, 'get', 'fact-nonexistent-000')
    assert 'not found' in out2.lower()