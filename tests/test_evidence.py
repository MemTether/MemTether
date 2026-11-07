"""Tests for Hindsight evidence grounding (2026-11-07)."""
import os
import pytest


class TestEntityEvidence:
    """get_entity_evidence: all active facts citing an entity, with uid+quote+timestamp."""

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEM_DB", str(tmp_path / "evidence.db"))
        monkeypatch.setenv("MEM_SKIP_VECTOR", "1")
        import importlib
        import gateway
        importlib.reload(gateway)
        gateway.HG = None
        gateway.init_db()
        return gateway

    def test_evidence_returns_all_facts(self, tmp_path, monkeypatch):
        """Entity with 3 facts returns 3 evidence items."""
        gw = self._setup(tmp_path, monkeypatch)
        gw.remember("webdb的端口是5432", source="codex")
        gw.remember("webdb的路径是/var/webdb", source="codex")
        gw.remember("webdb的管理员是admin", source="codex")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "evidence.db"))
        ev = gw.get_entity_evidence(conn, "webdb")
        conn.close()
        assert len(ev) >= 3, f"expected >= 3 evidence items, got: {len(ev)}"
        # Each has required fields
        for e in ev:
            assert "uid" in e and "quote" in e and "timestamp" in e and "source" in e

    def test_evidence_with_attr_filter(self, tmp_path, monkeypatch):
        """attr filter narrows evidence to matching attribute only."""
        gw = self._setup(tmp_path, monkeypatch)
        gw.remember("webdb的端口是5432", source="codex")
        gw.remember("webdb的路径是/var/webdb", source="codex")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "evidence.db"))
        ev = gw.get_entity_evidence(conn, "webdb", attr="端口")
        conn.close()
        assert len(ev) >= 1, f"expected evidence for 端口, got: {len(ev)}"
        for e in ev:
            assert "端口" in e["attr"], f"attr filter failed: {e['attr']}"

    def test_evidence_empty_for_unknown(self, tmp_path, monkeypatch):
        """Unknown entity returns empty list."""
        gw = self._setup(tmp_path, monkeypatch)
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "evidence.db"))
        ev = gw.get_entity_evidence(conn, "nonexistent_entity_xyz")
        conn.close()
        assert ev == []

    def test_evidence_excludes_superseded(self, tmp_path, monkeypatch):
        """Superseded facts do not appear in evidence."""
        gw = self._setup(tmp_path, monkeypatch)
        r1 = gw.remember("webdb的端口是5432", source="codex")
        gw.remember("webdb的路径是/var/webdb", source="codex")
        gw.correct(r1["uid"], "webdb的端口是5433", reason="changed")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "evidence.db"))
        ev = gw.get_entity_evidence(conn, "webdb")
        conn.close()
        # r1 is superseded, should not appear; but the new corrected fact should
        for e in ev:
            assert e["uid"] != r1["uid"], "superseded fact should not be in evidence"

    def test_extract_entities_from_content(self, tmp_path, monkeypatch):
        """extract_entities_from_content finds stored entities in text."""
        gw = self._setup(tmp_path, monkeypatch)
        gw.remember("webdb的端口是5432", source="codex")
        gw.remember("nginx的路径是/etc/nginx", source="codex")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "evidence.db"))
        entities = gw.extract_entities_from_content(conn, "webdb和nginx都是服务器")
        conn.close()
        assert "webdb" in entities, f"expected webdb, got: {entities}"
        assert "nginx" in entities, f"expected nginx, got: {entities}"

    def test_extract_no_match(self, tmp_path, monkeypatch):
        """Content with no stored entities returns empty list."""
        gw = self._setup(tmp_path, monkeypatch)
        gw.remember("webdb的端口是5432", source="codex")
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "evidence.db"))
        entities = gw.extract_entities_from_content(conn, "completely unrelated text about quantum")
        conn.close()
        assert entities == []
