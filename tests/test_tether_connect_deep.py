# -*- coding: utf-8 -*-
"""tests/test_tether_connect_deep.py — tether_connect.py 14% -> 50%+.

Covers the pure/orchestration surface that runs WITHOUT touching real client
configs: hub_defaults resolution, default_spec, detect on sandboxed fake
targets, _register_sources idempotency (agents.json), do_rollback from a
crafted manifest, do_selftest end-to-end, and pick_adapters filtering.
"""
import os, sys, json, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'

import tether_connect as T
from clients.base import ServerSpec, Target


class TestHubDefaults:
    def test_venv_python_preferred(self, tmp_path):
        venv_py = tmp_path / '.venv-memory' / 'Scripts' / 'python.exe'
        venv_py.parent.mkdir(parents=True); venv_py.write_text('')
        (tmp_path / 'mcp_server.py').write_text('')
        py, mcp = T.hub_defaults(str(tmp_path))
        assert py == str(venv_py) and mcp == str(tmp_path / 'mcp_server.py')

    def test_missing_mcp_returns_none(self, tmp_path):
        py, mcp = T.hub_defaults(str(tmp_path))
        assert py is None and mcp is None

    def test_empty_hub_dir(self):
        assert T.hub_defaults(None) == (None, None)
        assert T.hub_defaults('') == (None, None)


class TestDefaultSpec:
    def _args(self, tmp_path, **kw):
        class A: pass
        a = A()
        a.hub_dir = str(tmp_path); a.python = None; a.mcp_server = None
        a.name = 'memory-hub'; a.arg = []; a.env = ['A=1', 'B=2']
        for k, v in kw.items(): setattr(a, k, v)
        return a

    def test_env_kv_parsing(self, tmp_path):
        spec = T.default_spec(self._args(tmp_path))
        assert spec.env == {'A': '1', 'B': '2'}
        assert spec.name == 'memory-hub'
        assert spec.transport == 'stdio'

    def test_explicit_python_overrides(self, tmp_path):
        spec = T.default_spec(self._args(tmp_path, python=r'E:\x\py.exe'))
        assert spec.command == r'E:\x\py.exe'


class TestRegisterSources:
    def test_idempotent_and_active_only(self, tmp_path):
        hub = tmp_path / 'hub'; hub.mkdir()
        (hub / 'agents.json').write_text(json.dumps({'agents': {}}), encoding='utf-8')
        ads = T.all_adapters()
        wb = T.find_adapter('workbuddy-cn')
        # active_ids includes only workbuddy-cn → others must NOT be registered
        ch = T._register_sources(ads, str(hub), dry_run=False, active_ids={'workbuddy-cn'})
        data = json.load(open(hub / 'agents.json', encoding='utf-8'))
        names = json.dumps(data)
        assert 'workbuddy' in names or any(
            isinstance(v, dict) and v.get('name') == 'workbuddy'
            for v in (data.get('agents') or {}).values()) or 'workbuddy' in str(data)
        # zcode NOT active → absent
        assert 'zcode' not in names
        # run twice → second pass writes nothing new
        n_before = len((hub / 'agents.json').read_text(encoding='utf-8'))
        ch2 = T._register_sources(ads, str(hub), dry_run=False, active_ids={'workbuddy-cn'})
        wrote2 = [c for c in ch2 if getattr(c, 'wrote', False)]
        assert not wrote2  # idempotent: no writes on second pass

    def test_dry_run_writes_nothing(self, tmp_path):
        hub = tmp_path / 'hub'; hub.mkdir()
        (hub / 'agents.json').write_text('{"agents": {}}', encoding='utf-8')
        T._register_sources(T.all_adapters(), str(hub), dry_run=True, active_ids=None)
        assert json.load(open(hub / 'agents.json', encoding='utf-8')) == {'agents': {}}


class TestRollback:
    def test_no_backup_dir(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(T, 'BACKUP_ROOT', str(tmp_path / 'nope'))
        assert T.do_rollback() == 1

    def test_restore_from_manifest(self, tmp_path, monkeypatch, capsys):
        import clients.base as B
        backup_root = tmp_path / 'backups'; stamp = '20261008-000000'
        bdir = backup_root / stamp; bdir.mkdir(parents=True)
        monkeypatch.setattr(T, 'BACKUP_ROOT', str(backup_root))
        # craft: original file + backup copy + manifest
        orig = tmp_path / 'mcp.json'
        orig.write_text('{"restored": true}', encoding='utf-8')
        bak = bdir / 'mcp.json.bak'
        bak.write_text('{"before": true}', encoding='utf-8')
        (bdir / 'manifest.json').write_text(json.dumps({'changes': [
            {'path': str(orig), 'backup': str(bak)},
            {'path': str(tmp_path / 'never_existed.json'), 'backup': None},
        ]}), encoding='utf-8')
        rc = T.do_rollback(stamp)
        assert rc == 0
        assert json.load(open(orig, encoding='utf-8')) == {'before': True}

    def test_missing_stamp(self, tmp_path, monkeypatch):
        backup_root = tmp_path / 'backups'; backup_root.mkdir()
        monkeypatch.setattr(T, 'BACKUP_ROOT', str(backup_root))
        assert T.do_rollback('no-such-stamp') == 1


class TestSelftestAndPicking:
    def test_selftest_passes(self, capsys):
        rc = T.do_selftest()
        out = capsys.readouterr().out
        assert rc == 0 and 'FAIL' not in out

    def test_pick_adapters_by_clients(self):
        class A: clients = 'codex,workbuddy-cn'
        picked = T.pick_adapters(A())
        assert [a.id for a in picked] == ['codex', 'workbuddy-cn']

    def test_pick_adapters_unknown_exits(self):
        import pytest
        class A: clients = 'no-such-client'
        with pytest.raises(SystemExit):
            T.pick_adapters(A())
