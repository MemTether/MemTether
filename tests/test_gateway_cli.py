# -*- coding: utf-8 -*-
"""tests/test_gateway_cli.py — gateway.main() CLI smoke coverage (a74).

Covers: remember / search / init / stats / correct / retire via monkeypatched
sys.argv. Uses MEM_DB + MEM_SKIP_VECTOR isolation (same pattern as
test_gateway_deep.py) so no real data is touched.
"""
import os
import sys
import json
import pytest

os.environ['MEM_SKIP_VECTOR'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _setup(tmp_path):
    db = str(tmp_path / 'cli.db')
    os.environ['MEM_DB'] = db
    import importlib, gateway
    importlib.reload(gateway)
    gateway.init_db()
    return gateway


def _run(gw, monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, 'argv', ['gateway.py'] + list(args))
    gw.main()
    return capsys.readouterr().out


def test_cli_remember_then_search(tmp_path, monkeypatch, capsys):
    gw = _setup(tmp_path)
    out = _run(gw, monkeypatch, capsys, 'remember', 'CLI测试 alpha 的端口是 8080',
               '--type', 'fact', '--source', 'codex')
    assert 'uid' in out or 'ok' in out.lower()

    out2 = _run(gw, monkeypatch, capsys, 'search', 'alpha 端口', '--limit', '5')
    assert len(out2) > 0


def test_cli_init_stats(tmp_path, monkeypatch, capsys):
    gw = _setup(tmp_path)
    _run(gw, monkeypatch, capsys, 'init')
    out = _run(gw, monkeypatch, capsys, 'stats')
    assert len(out) > 0


def test_cli_correct_retire_roundtrip(tmp_path, monkeypatch, capsys):
    gw = _setup(tmp_path)
    out = _run(gw, monkeypatch, capsys, 'remember', '旧结论: 服务A端口3000',
               '--source', 'codex')
    uid = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith('{'):
            try:
                uid = json.loads(s).get('uid')
                break
            except Exception:
                pass
    assert uid, 'expected JSON uid from remember, got %r' % out[:200]

    out2 = _run(gw, monkeypatch, capsys, 'correct', uid, '新结论: 服务A端口4000',
                '--reason', 'port changed')
    assert 'ok' in out2.lower() or 'new_uid' in out2

    out3 = _run(gw, monkeypatch, capsys, 'retire', uid, '--reason', 'obsolete')
    assert 'ok' in out3.lower() or 'retired' in out3.lower()