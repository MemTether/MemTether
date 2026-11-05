# -*- coding: utf-8 -*-
"""Line-level deep-audit round 6b: tether_connect doctor/apply/verify.

The user-facing connection doctor (do_doctor 80 dark lines) + apply pipeline
+ verify, tested with fake adapters against tmp dirs - no real clients needed.
"""
import os
import sqlite3
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MEM_SKIP_VECTOR", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import tether_connect as tc


class _FakeAdapter:
    """Minimal adapter: one installed candidate whose config file we control."""
    id = "fake-client"
    display = "Fake Client"
    source = "fake_src"
    needs_restart = False
    gotchas = ""

    def __init__(self, cfg_path, installed=True, read_ok=True):
        self.cfg_path = cfg_path
        self.installed = installed
        self.read_ok = read_ok
        self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg_path.write_text('{"mcpServers": {}}', encoding="utf-8")

    def candidates(self):
        return [("user", str(self.cfg_path))]

    def probe(self, scope, path):
        return (self.installed, "probe")

    def read_back(self, target, spec):
        if self.read_ok:
            return True, "config matches"
        return False, "config mismatch"

    def write(self, target, spec, dry_run=True):
        from clients.base import Change
        if dry_run:
            return Change(target, "skip", "dry-run")
        self.cfg_path.write_text('{"mcpServers": {"memory-hub": {}}}', encoding="utf-8")
        return Change(target, "replace", "written")

    def trust(self, spec, dry_run=True):
        return (True, "trusted")


def _spec(tmp_path):
    mcp = ROOT + os.sep + "mcp_server.py"
    return tc.ServerSpec(name="memory-hub-test", command=sys.executable,
                         args=[mcp], env={}, transport="stdio")


class TestDoctor:
    def test_all_layers_reported_no_clients(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "doctor.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE facts (x)")
        conn.execute("CREATE TABLE audit_log (x)")
        conn.commit(); conn.close()
        monkeypatch.setenv("MEM_DB", str(db))
        rc = tc.do_doctor([], _spec(tmp_path), client=None)
        out = capsys.readouterr().out
        assert rc == 0, "SKIP rows are not FAILs: no-clients env is READY"
        assert "2 SKIP" in out

    def test_layers_pass_with_real_db_and_mcp(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "doctor2.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE facts (x)")
        conn.execute("CREATE TABLE audit_log (x)")
        conn.commit(); conn.close()
        monkeypatch.setenv("MEM_DB", str(db))
        rc = tc.do_doctor([], _spec(tmp_path), client=None)
        out = capsys.readouterr().out
        assert "L1 python" in out and "L3 db" in out and "L4 mcp" in out
        assert "[PASS] L3 db" in out
        # no FAIL rows -> status READY
        assert "status: READY" in out

    def test_db_missing_reports_fail(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "nope.db"))
        rc = tc.do_doctor([], _spec(tmp_path), client=None)
        out = capsys.readouterr().out
        assert "DB 不存在" in out
        assert rc == 1


class TestApplyVerify:
    def test_apply_dry_run_writes_nothing(self, tmp_path, capsys):
        cfg = tmp_path / "cfg" / "config.json"
        ad = _FakeAdapter(cfg)
        r = tc.do_apply([ad], _spec(tmp_path), dry_run=True, yes=True)
        assert cfg.read_text(encoding="utf-8") == '{"mcpServers": {}}', \
            "dry-run must not write"

    def test_apply_writes_and_reports_backup(self, tmp_path, capsys):
        cfg = tmp_path / "cfg2" / "config.json"
        ad = _FakeAdapter(cfg)
        r = tc.do_apply([ad], _spec(tmp_path), dry_run=False, yes=True)
        assert '{"memory-hub"' in cfg.read_text(encoding="utf-8")
        out = capsys.readouterr().out
        assert "manifest.json" in out and "回滚：python tether_connect.py rollback" in out
        assert r and r[0].wrote

    def test_apply_skips_uninstalled(self, tmp_path, capsys):
        cfg = tmp_path / "cfg3" / "config.json"
        ad = _FakeAdapter(cfg, installed=False)
        r = tc.do_apply([ad], _spec(tmp_path), dry_run=False, yes=True)
        assert cfg.read_text(encoding="utf-8") == '{"mcpServers": {}}'
        out = capsys.readouterr().out
        assert "未发现该客户端安装痕迹" in out

    def test_verify_pass_and_fail(self, tmp_path, capsys):
        ok_ad = _FakeAdapter(tmp_path / "v1" / "c.json", read_ok=True)
        bad_ad = _FakeAdapter(tmp_path / "v2" / "c.json", read_ok=False)
        rc = tc.do_verify([ok_ad, bad_ad], _spec(tmp_path))
        out = capsys.readouterr().out
        assert "[PASS] fake-client" in out
        assert "[FAIL] fake-client" in out
        assert rc == 1

    def test_register_sources_only_active(self, tmp_path, capsys):
        hub = tmp_path / "hub"
        hub.mkdir()
        (hub / "agents.json").write_text('{"agents": {}}', encoding="utf-8")
        cfg = tmp_path / "cfg4" / "c.json"
        ad = _FakeAdapter(cfg)
        from clients.base import Change, Target
        t = Target(ad.id, ad.display, "user", str(cfg), True, True, "")
        ch = Change(t, "replace", "written")
        r = tc._register_sources([ad], str(hub), dry_run=False, active_ids={ad.id})
        data = __import__("json").loads((hub / "agents.json").read_text(encoding="utf-8"))
        assert "fake_src" in data["agents"]
        # a second, NOT-active adapter must not be registered
        other = _FakeAdapter(tmp_path / "cfg5" / "c.json")
        other.source = "inactive_src"
        r2 = tc._register_sources([other], str(hub), dry_run=False, active_ids=set())
        data2 = __import__("json").loads((hub / "agents.json").read_text(encoding="utf-8"))
        assert "inactive_src" not in data2["agents"]
