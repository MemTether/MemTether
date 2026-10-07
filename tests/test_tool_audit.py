# -*- coding: utf-8 -*-
"""tests/test_tool_audit.py — tool_audit.py coverage (was 0%).

README previously claimed tool_audit without any test (v19 tech-debt table).
Module loads assets.local.json (gitignored → [] in CI); we monkeypatch ASSETS.
DB is a module constant; tests point it at a temp schema matching gateway.
"""
import os, sys, json, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['MEM_SKIP_VECTOR'] = '1'

import tool_audit as T


def _db_with_tool_assets(tmpdir):
    db = os.path.join(str(tmpdir), 't.db')
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE tool_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE,
        name TEXT, aliases TEXT, type TEXT, status TEXT DEFAULT 'active',
        path TEXT, entrypoint TEXT, capabilities TEXT, recipe_ids TEXT,
        known_failures TEXT, prerequisites TEXT, last_verified_at TEXT,
        verification_method TEXT, source TEXT, created_at TEXT, updated_at TEXT,
        q_value REAL DEFAULT 0.5, use_count INTEGER DEFAULT 0)""")
    conn.commit(); conn.close()
    return db


class TestRunVerify:
    def test_file_flag(self, tmp_path):
        f = tmp_path / 'a.txt'; f.write_text('x')
        assert T._run_verify('test -f "%s"' % f, str(f)) is True
        assert T._run_verify('test -f "%s"' % (tmp_path / 'nope.txt'), 'x') is False

    def test_dir_flag(self, tmp_path):
        d = tmp_path / 'sub'; d.mkdir()
        assert T._run_verify('test -d "%s"' % d, str(d)) is True
        assert T._run_verify('test -d "%s"' % (tmp_path / 'nope'), 'x') is False

    def test_exists_flag(self, tmp_path):
        f = tmp_path / 'b.txt'; f.write_text('x')
        assert T._run_verify('test -e "%s"' % f, str(f)) is True

    def test_unknown_cmd_falls_back_to_path(self, tmp_path):
        f = tmp_path / 'c.txt'; f.write_text('x')
        assert T._run_verify('weird command', str(f)) is True
        assert T._run_verify('weird command', str(tmp_path / 'nope')) is False


class TestAuditSeedVerify:
    def _patch(self, tmp_path, monkeypatch, assets):
        db = _db_with_tool_assets(str(tmp_path))
        monkeypatch.setattr(T, 'DB', db)
        monkeypatch.setattr(T, 'ASSETS', assets)
        monkeypatch.setattr(T, '_ASSETS_F', str(tmp_path / 'assets.local.json'))
        return db

    def test_audit_no_assets_reports_config_missing(self, tmp_path, monkeypatch, capsys):
        self._patch(tmp_path, monkeypatch, [])
        T.audit()
        out = capsys.readouterr().out
        assert '未配置' in out

    def test_seed_and_verify_roundtrip(self, tmp_path, monkeypatch, capsys):
        marker = tmp_path / 'tool_a.py'; marker.write_text('x')
        missing = tmp_path / 'gone.py'
        assets = [
            ['tool_a', str(marker), 'script', '', 'test -f "%s"' % marker, 'cap', 'note'],
            ['tool_b', str(missing), 'script', '', 'test -f "%s"' % missing, 'cap', 'note'],
        ]
        db = self._patch(tmp_path, monkeypatch, assets)
        T.seed()
        conn = sqlite3.connect(db)
        rows = {r[0]: r[1] for r in conn.execute("SELECT name, status FROM tool_assets")}
        conn.close()
        assert rows == {'tool_a': 'active', 'tool_b': 'missing'}

        # now create tool_b's file and verify() should flip it to active
        missing.write_text('x')
        good, bad = T.verify()
        assert good == 2 and bad == 0
        conn = sqlite3.connect(db)
        status = conn.execute("SELECT status FROM tool_assets WHERE name='tool_b'").fetchone()[0]
        conn.close()
        assert status == 'active'

    def test_verify_empty_db(self, tmp_path, monkeypatch, capsys):
        self._patch(tmp_path, monkeypatch, [])
        good, bad = T.verify()
        assert (good, bad) == (0, 0)
